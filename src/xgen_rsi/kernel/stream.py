"""동기 다리 — 엔진을 워커 스레드의 전용 이벤트 루프에서 돌리고 외부 청크로 번역한다.

외부 계약(조사 11 문서 §3)을 그대로 지킨다.

* 청크 문법: ``str`` | ``{"type":"agent_event"}`` | ``{"type":"canvas_command"}`` | ``{"type":"usage"}``
* 텍스트·도구 사건은 엔진 사건 순서 그대로, 같은 호출의 ``tool_call`` 이 ``tool_result|tool_error`` 앞
* 이어가기(``max_continuation_slices``)·``task_progress``/``task_suspended``/``task_blocked``·안내 문장
* usage 청크는 끝에 **정확히 1회**, ``usage_sink`` 는 같은 객체를 update(닫혀도 ``partial`` 로 채움)
* ``.close()`` → 엔진 정리 → 실행 기록 → 기억 닫기·증류 → ``on_close``
* 대기 중에도 취소를 듣는다(0.2초 주기)

청크를 만드는 함수는 기존 ``host.runner`` 의 것과 같은 모양을 낸다(필드·자르기 규칙). 사용자에게 보이는 안내
문장은 기존 모듈의 상수를 그대로 가져온다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

from xgen_rsi.base.core.run_status import RunStatus

logger = logging.getLogger(__name__)

_DISPLAY_RESULT_LIMIT = 4000
_DISPLAY_TAIL_KEEP = 800
_CANCEL_POLL_S = 0.2
_SENTINEL = object()

Chunk = Union[str, Dict[str, Any]]


class _CancelRequested(Exception):
    """대기 중에 정지가 들어왔다."""


def _indicator(tool_name: str) -> Optional[Dict[str, Any]]:
    try:
        from xgen_rsi.base.host.tool_indicators import get_indicator

        return get_indicator(tool_name)
    except Exception:  # noqa: BLE001
        return None


def _attach_tool_use_id(event: Dict[str, Any], tool_use_id: Any) -> None:
    if tool_use_id is None:
        return
    value = tool_use_id if isinstance(tool_use_id, str) else str(tool_use_id)
    if not value:
        return
    event["tool_use_id"] = value
    event["run_id"] = value


def tool_call_event(name: str, tool_input: Any, *, tool_use_id: Any = None) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "type": "tool_call",
        "tool_name": name,
        "tool_input": tool_input if isinstance(tool_input, str) else json.dumps(tool_input or {}, ensure_ascii=False, default=str),
        "timestamp": datetime.now().isoformat(),
    }
    _attach_tool_use_id(event, tool_use_id)
    indicator = _indicator(name)
    if indicator:
        event["indicator"] = indicator
    return event


def display_result(text: str) -> str:
    """머리+꼬리를 남기는 축약 — 문서 도구의 다운로드 마커가 결과 **끝**에 있다."""
    if len(text) <= _DISPLAY_RESULT_LIMIT:
        return text
    head = _DISPLAY_RESULT_LIMIT - _DISPLAY_TAIL_KEEP
    omitted = len(text) - head - _DISPLAY_TAIL_KEEP
    return f"{text[:head]}\n…[{omitted} chars truncated]…\n{text[-_DISPLAY_TAIL_KEEP:]}"


def tool_end_event(
    name: str,
    result_text: str,
    *,
    is_error: bool = False,
    duration_ms: Optional[int] = None,
    tool_use_id: Any = None,
) -> Dict[str, Any]:
    if is_error:
        event: Dict[str, Any] = {"type": "tool_error", "tool_name": name, "error": result_text or "tool execution failed"}
    else:
        event = {
            "type": "tool_result",
            "tool_name": name,
            "result": display_result(result_text),
            "result_length": len(result_text),
            "citations": None,
        }
    event["timestamp"] = datetime.now().isoformat()
    if duration_ms is not None:
        event["duration_ms"] = duration_ms
    _attach_tool_use_id(event, tool_use_id)
    indicator = _indicator(name)
    if indicator:
        event["indicator"] = indicator
    return event


def stringify_content(content: Any) -> str:
    """CLI 도구 결과 블록 → 사람이 읽는 텍스트(봉투를 벗긴다)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return stringify_content([content])
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = str(block.get("type") or "")
            if btype == "text" or "text" in block:
                parts.append(str(block.get("text") or ""))
            elif btype:
                parts.append(f"[{btype}]")
            else:
                try:
                    parts.append(json.dumps(block, ensure_ascii=False, default=str))
                except (TypeError, ValueError):
                    parts.append(str(block))
        joined = "\n".join(p for p in parts if p)
        if joined:
            return joined
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def _notify_loop(on_loop: Optional[Callable[[Any], None]], loop: Any) -> None:
    if on_loop is None:
        return
    try:
        on_loop(loop)
    except Exception:  # noqa: BLE001
        logger.warning("rsi: on_loop 콜백 실패 (무시)", exc_info=True)


def _next_event(loop: Any, queue: asyncio.Queue, task: asyncio.Task, cancel_check: Optional[Callable[[], bool]]) -> Any:
    """다음 사건을 기다리면서도 정지를 듣는다. 정지면 엔진 작업을 취소한다."""
    getter = loop.create_task(queue.get())
    try:
        while True:
            done, _ = loop.run_until_complete(asyncio.wait({getter}, timeout=_CANCEL_POLL_S))
            if done:
                return getter.result()
            if cancel_check is not None:
                try:
                    wants_stop = bool(cancel_check())
                except Exception:  # noqa: BLE001
                    wants_stop = False
                if wants_stop:
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        loop.run_until_complete(task)
                    raise _CancelRequested()
    finally:
        if not getter.done():
            getter.cancel()
            with contextlib.suppress(BaseException):
                loop.run_until_complete(getter)


class TurnDriver:
    """준비된 턴(엔진·계획·원장)을 돌려 외부 출력으로 만든다."""

    def __init__(self, prepared: Any, plan: Any, host: Any) -> None:
        self.p = prepared
        self.plan = plan
        self.host = host

    # ── 공통 ────────────────────────────────────────────────────────────
    def _usage(self) -> Optional[Dict[str, Any]]:
        from xgen_rsi.base.host.harness_components import harness_summary

        state = self.plan.state
        last = getattr(state, "last_api_response", None)
        model = str(getattr(last, "model", "") or "") or str(getattr(state, "model", "") or "")
        provider = self.p.provider_label
        return self.p.ledger.external_usage_payload(
            model=model,
            provider=provider,
            turn_cost_usd=float(getattr(state, "total_cost_usd", 0.0) or 0.0),
            harness=harness_summary(state),
        )

    async def _slice_task(self, pipeline_input: Any, continuation: bool) -> None:
        """슬라이스 하나를 돌리고 수명 사건을 낸다(기존 run_stream 의 pipeline.complete/error 와 같은 자료)."""
        from xgen_rsi.base.core.errors import ExecutorErrorCode, GenyExecutorError

        hub = self.p.hub
        state = self.plan.state
        try:
            outcome = await self.p.engine.run_slice(pipeline_input, continuation=continuation)
            hub.publish(
                "pipeline.complete",
                {
                    "result": outcome.final_text,
                    "iterations": state.iteration,
                    "total_cost_usd": state.total_cost_usd,
                    "status": outcome.status,
                    "termination_reason": outcome.termination_reason,
                    "resumable": outcome.resumable,
                    "checkpoint_id": outcome.checkpoint_id,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 실패는 사건으로, 예외로 새지 않는다
            state.mark_failed(str(exc))
            code = ExecutorErrorCode.EXEC_UNKNOWN.value
            if isinstance(exc, GenyExecutorError) and exc.code is not None:
                code = exc.code.value
            hub.publish(
                "pipeline.error",
                {
                    "error": str(exc),
                    "code": code,
                    "exception_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "total_cost_usd": state.total_cost_usd,
                },
            )
            logger.warning("rsi: slice failed", exc_info=True)
        finally:
            q = hub._queue
            if q is not None:
                q.put_nowait(_SENTINEL)

    def _start_slice(self, loop: Any, pipeline_input: Any, continuation: bool) -> tuple:
        self.p.hub.new_run()
        queue = self.p.hub.open_queue()
        self.p.hub.publish("pipeline.start", {"input": str(pipeline_input)[:200]})
        task = loop.create_task(self._slice_task(pipeline_input, continuation))
        return queue, task

    # ── 스트리밍 ────────────────────────────────────────────────────────
    def stream(self) -> Iterator[Chunk]:
        from xgen_rsi.base.host.runner import SUSPEND_NOTICE, _stop_notice

        plan = self.plan
        state = plan.state
        tool_events = plan.tool_events
        result_sink = plan.result_sink or {}
        cancel_check = plan.cancelled
        output_schema = plan.schema
        usage_sink = plan.usage_sink
        loop = asyncio.new_event_loop()
        _notify_loop(plan.on_loop, loop)
        task: Optional[asyncio.Task] = None
        cli_tool_names: Dict[str, str] = {}
        cli_tool_started: Dict[str, float] = {}
        last_message_chunk = ""
        turn_started = time.monotonic()
        out_parts: List[str] = []
        turn_error = ""
        turn_completed = False
        task_status = RunStatus.RUNNING.value
        try:
            self.p.open_rollout(loop)
            next_input: Any = plan.pipeline_input
            continuation = False
            continuation_count = 0
            cancelled = False
            max_continuations = max(0, int(plan.max_continuation_slices))
            while True:
                slice_streamed_text = False
                slice_resumable = False
                slice_reason = ""
                queue, task = self._start_slice(loop, next_input, continuation)
                while True:
                    if cancel_check is not None and cancel_check():
                        cancelled = True
                        break
                    try:
                        event = _next_event(loop, queue, task, cancel_check)
                    except _CancelRequested:
                        cancelled = True
                        break
                    if event is _SENTINEL:
                        break
                    etype = event.type
                    data = event.data
                    if etype == "text.delta":
                        chunk = data.get("text", "")
                        if chunk:
                            if data.get("granularity") == "message":
                                normalized = str(chunk).strip()
                                if normalized and normalized == last_message_chunk:
                                    slice_streamed_text = True
                                    continue
                                last_message_chunk = normalized
                            slice_streamed_text = True
                            out_parts.append(chunk)
                            yield chunk
                    elif etype == "pipeline.complete":
                        task_status = str(data.get("status") or RunStatus.COMPLETED.value)
                        slice_resumable = bool(data.get("resumable"))
                        slice_reason = str(data.get("termination_reason") or "")
                        result = data.get("result", "")
                        if not slice_streamed_text and result:
                            out_parts.append(result)
                            yield result
                    elif etype == "pipeline.error":
                        task_status = RunStatus.FAILED.value
                        turn_error = str(data.get("error", "unknown error"))
                        yield f"\n[ERROR] {turn_error}"
                    elif tool_events and etype == "tool.call_start":
                        yield {
                            "type": "agent_event",
                            "data": tool_call_event(data.get("name", ""), data.get("input"), tool_use_id=data.get("tool_use_id")),
                        }
                    elif tool_events and etype == "tool.call_complete":
                        name = data.get("name", "")
                        yield {
                            "type": "agent_event",
                            "data": tool_end_event(
                                name,
                                str(data.get("error") or "") or str(data.get("result") or "") or result_sink.get(name, ""),
                                is_error=bool(data.get("is_error")),
                                duration_ms=data.get("duration_ms"),
                                tool_use_id=data.get("tool_use_id"),
                            ),
                        }
                    elif etype == "canvas_command":
                        yield {"type": "canvas_command", "data": data}
                    elif tool_events and etype == "api.cli_tool_call":
                        name = data.get("name", "") or "cli_tool"
                        tool_use_id = data.get("id") or ""
                        if tool_use_id:
                            cli_tool_names[tool_use_id] = name
                            cli_tool_started[tool_use_id] = time.monotonic()
                        yield {"type": "agent_event", "data": tool_call_event(name, data.get("input"), tool_use_id=tool_use_id)}
                    elif tool_events and etype == "api.tool_result" and data.get("source") == "cli":
                        tool_use_id = data.get("tool_use_id") or ""
                        name = cli_tool_names.pop(tool_use_id, "") or "cli_tool"
                        started_at = cli_tool_started.pop(tool_use_id, None)
                        yield {
                            "type": "agent_event",
                            "data": tool_end_event(
                                name,
                                stringify_content(data.get("content")),
                                is_error=bool(data.get("is_error")),
                                duration_ms=int((time.monotonic() - started_at) * 1000) if started_at is not None else None,
                                tool_use_id=tool_use_id,
                            ),
                        }
                self.p.hub.close_queue()
                if cancelled:
                    break
                if task is not None:
                    with contextlib.suppress(BaseException):
                        loop.run_until_complete(task)
                task = None
                if slice_resumable and continuation_count < max_continuations:
                    continuation_count += 1
                    yield {
                        "type": "agent_event",
                        "data": {
                            "type": "task_progress",
                            "status": "continuing",
                            "reason": slice_reason,
                            "slice": continuation_count,
                            "timestamp": datetime.now().isoformat(),
                        },
                    }
                    continuation = True
                    continue
                if slice_resumable:
                    if output_schema is None:
                        out_parts.append(SUSPEND_NOTICE)
                        yield SUSPEND_NOTICE
                    yield {
                        "type": "agent_event",
                        "data": {
                            "type": "task_suspended",
                            "status": RunStatus.SUSPENDED.value,
                            "reason": slice_reason,
                            "resumable": True,
                            "checkpoint_id": state.checkpoint_id,
                            "timestamp": datetime.now().isoformat(),
                        },
                    }
                elif task_status == RunStatus.BLOCKED.value:
                    yield {
                        "type": "agent_event",
                        "data": {
                            "type": "task_blocked",
                            "status": RunStatus.BLOCKED.value,
                            "reason": slice_reason,
                            "resumable": False,
                            "timestamp": datetime.now().isoformat(),
                        },
                    }
                elif output_schema is None and (notice := _stop_notice(state)):
                    out_parts.append(notice)
                    yield notice
                turn_completed = True
                break
            usage = self._usage()
            if usage is not None:
                if not turn_completed:
                    usage = {**usage, "partial": True}
                if usage_sink is not None:
                    usage_sink.update(usage)
                yield {"type": "usage", "data": usage}
        finally:
            if usage_sink is not None and not usage_sink:
                try:
                    closed_usage = self._usage()
                    if closed_usage is not None:
                        usage_sink.update({**closed_usage, "partial": True})
                except Exception:  # noqa: BLE001
                    logger.debug("rsi: usage aggregation on close failed", exc_info=True)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    loop.run_until_complete(task)
            self.p.hub.close_queue()
            _notify_loop(plan.on_loop, None)
            self.p.close_rollout(loop)
            turn_failed = bool(turn_error) or not turn_completed or task_status != RunStatus.COMPLETED.value
            self.p.finish_turn(
                loop,
                host=self.host,
                input_text=plan.pipeline_input,
                output_text="".join(out_parts),
                success=not turn_failed,
                duration_ms=int((time.monotonic() - turn_started) * 1000),
                error=(
                    turn_error
                    or ("cancelled" if not turn_completed else "")
                    or (task_status if task_status != RunStatus.COMPLETED.value else "")
                ),
                cancelled=not turn_completed and not turn_error,
                produced_output=bool(out_parts),
                unfinished=("error" if turn_error else "cancelled") if (not turn_completed or turn_error) else "",
            )
            loop.close()
            try:
                plan.teardown()
            except Exception:  # noqa: BLE001
                pass

    # ── 비스트리밍 ──────────────────────────────────────────────────────
    def run(self) -> str:
        from xgen_rsi.base.host.runner import _stop_notice

        plan = self.plan
        state = plan.state
        loop = asyncio.new_event_loop()
        _notify_loop(plan.on_loop, loop)
        turn_started = time.monotonic()
        turn_output = ""
        turn_success = False
        turn_error = ""
        produced_output = False
        try:
            self.p.open_rollout(loop)
            outcome_holder: Dict[str, Any] = {}
            next_input: Any = plan.pipeline_input
            continuation = False
            continuation_count = 0
            max_continuations = max(0, int(plan.max_continuation_slices))
            while True:
                queue, task = self._start_slice(loop, next_input, continuation)
                while True:
                    event = loop.run_until_complete(queue.get())
                    if event is _SENTINEL:
                        break
                    if event.type == "pipeline.complete":
                        outcome_holder = {"ok": True, **event.data}
                    elif event.type == "pipeline.error":
                        outcome_holder = {"ok": False, **event.data}
                self.p.hub.close_queue()
                loop.run_until_complete(task)
                if outcome_holder.get("ok") and outcome_holder.get("resumable") and continuation_count < max_continuations:
                    continuation_count += 1
                    continuation = True
                    continue
                break
            produced_output = bool(outcome_holder.get("result") or "")
            usage_sink = plan.usage_sink
            if usage_sink is not None:
                try:
                    usage = self._usage()
                    if usage is not None:
                        usage_sink.update(usage)
                except Exception:  # noqa: BLE001
                    logger.debug("rsi: usage aggregation failed", exc_info=True)
            if not outcome_holder.get("ok"):
                turn_error = str(outcome_holder.get("error") or "unknown error")
                return f"[ERROR] {outcome_holder.get('error')}"
            status = str(outcome_holder.get("status") or RunStatus.FAILED.value)
            if status == RunStatus.SUSPENDED.value:
                reason = str(outcome_holder.get("termination_reason") or "") or "slice_limit"
                turn_error = f"suspended: {reason}"
                return f"[SUSPENDED] {reason}"
            if status == RunStatus.BLOCKED.value:
                reason = str(outcome_holder.get("termination_reason") or "") or "blocked"
                turn_error = f"blocked: {reason}"
                return f"[BLOCKED] {reason}"
            if status != RunStatus.COMPLETED.value:
                detail = str(getattr(state, "completion_detail", "") or status)
                turn_error = detail
                return f"[ERROR] {detail}"
            final = str(outcome_holder.get("result") or "")
            if plan.schema:
                final = self.p.output_settle(final, plan.schema)
            else:
                final += _stop_notice(state)
            turn_output = final
            turn_success = True
            return final
        except BaseException as exc:
            turn_error = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            self.p.hub.close_queue()
            _notify_loop(plan.on_loop, None)
            self.p.close_rollout(loop)
            self.p.finish_turn(
                loop,
                host=self.host,
                input_text=plan.pipeline_input,
                output_text=turn_output,
                success=turn_success,
                duration_ms=int((time.monotonic() - turn_started) * 1000),
                error=turn_error,
                cancelled=False,
                produced_output=produced_output or bool(turn_output),
                unfinished="" if turn_success else ("cancelled" if "Cancel" in turn_error else "error"),
            )
            loop.close()
