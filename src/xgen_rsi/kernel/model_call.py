"""정책 호출 게이트웨이 — 요청 조립, 스트림 소비, 재시도, 시간 상한, 호출 이벤트.

기존 Stage 6(``stages/s06_api``)의 호출 계약을 커널로 옮긴 것이다. 같은 이벤트 이름(``api.request``,
``api.response``, ``api.error``, ``api.retry``, ``api.stream_restart``, ``api.ttft``, ``text.delta``,
``thinking.delta``, ``api.tool_use``, ``api.cli_tool_call``, ``api.tool_result``)을 내므로 하네스
관측 카운터(``usage.harness``)와 스트림 번역이 그대로 맞는다. 바뀐 점은 셋이다.

* 모든 호출이 :class:`~xgen_rsi.kernel.ledger.LedgerClient` 를 지나 원장에 남는다(purpose 포함).
* 모델 라우팅·페일오버를 하지 않는다 — RRSI 는 정책 π 를 고정한다(설계 31 §3.2 평가 모드 불변식).
* 루프 결정이 없다. 호출은 응답만 돌려주고, 계속할지는 control_flow 구성요소가 정한다.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from xgen_agent_runtime.core.errors import APIError, ErrorCategory, ExecutorErrorCode
from xgen_agent_runtime.core.message_repair import (
    normalize_messages_for_request,
    retire_tool_calls_by_name,
)
from xgen_agent_runtime.core.shared_keys import SharedKeys
from xgen_agent_runtime.llm_client import timeouts as _timeouts
from xgen_agent_runtime.stages.s06_api.artifact.default.retry import ExponentialBackoffRetry

#: "모델이 무언가를 내놓았다" 로 치는 청크 — 첫 응답 감시는 이것이 올 때까지 잰다.
_CONTENT_CHUNK_TYPES = frozenset({"text_delta", "thinking_delta", "tool_use", "input_json_delta"})


async def watched_stream(
    stream: AsyncIterator[Dict[str, Any]],
    *,
    first_chunk_s: float,
    idle_s: float,
) -> AsyncIterator[Dict[str, Any]]:
    """스트림을 흘려보내되 멎으면 끊는다(첫 내용까지 ``first_chunk_s``, 그 뒤 청크 사이 ``idle_s``)."""
    iterator = stream.__aiter__()
    deadline = time.monotonic() + first_chunk_s
    seen_content = False
    try:
        while True:
            wait = idle_s if seen_content else max(0.05, deadline - time.monotonic())
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=wait)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                if seen_content:
                    detail = f"모델 응답이 {idle_s:g}초 동안 멈췄습니다"
                else:
                    detail = f"모델이 {first_chunk_s:g}초 안에 응답을 시작하지 않았습니다"
                raise APIError(
                    detail,
                    category=ErrorCategory.TIMEOUT,
                    code=ExecutorErrorCode.EXEC_API_TIMEOUT,
                ) from None
            if not seen_content and isinstance(chunk, dict) and chunk.get("type") in _CONTENT_CHUNK_TYPES:
                seen_content = True
            yield chunk
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass


def inject_turn_context(messages: List[Dict[str, Any]], context_text: str) -> List[Dict[str, Any]]:
    """이번 턴 맥락(시각·검색 기억·턴 안내)을 **요청 사본의** 이번 턴 사용자 말에 붙인다(기록은 그대로).

    기존 엔진의 함수를 그대로 부른다 — 자리 규칙(도구 결과가 아니라 사용자 말에, 4.78.0)이 갈라지지 않게.
    """
    from xgen_agent_runtime.stages.s06_api.artifact.default.stage import APIStage

    return APIStage._inject_turn_context(messages, context_text)


class ModelCaller:
    """정책 호출 하나를 책임진다. 상태(PipelineState)는 요청 재료와 이벤트 출구로만 쓴다."""

    def __init__(
        self,
        client: Any,
        *,
        stream: bool = True,
        max_retries: int = 3,
        timeout_ms: Optional[int] = None,
    ) -> None:
        self.client = client
        self.stream = bool(stream)
        self._retry = ExponentialBackoffRetry(max_retries=max_retries)
        self._timeout_ms = timeout_ms

    @property
    def provider(self) -> str:
        return str(getattr(self.client, "provider", "") or "")

    @property
    def is_subprocess(self) -> bool:
        return bool(getattr(getattr(self.client, "capabilities", None), "is_subprocess", False))

    # ── 공개 ────────────────────────────────────────────────────────────
    async def call(
        self,
        state: Any,
        model_config: Any,
        *,
        purpose: str = "main",
        extra_messages: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        system: Any = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: Optional[bool] = None,
        emit_text: bool = True,
    ) -> Any:
        """요청을 보내고 :class:`APIResponse` 를 돌려준다. 실패는 :class:`APIError`.

        ``messages``·``system``·``tools`` 를 주지 않으면 턴 기록(state)의 것을 쓴다 — 메인 루프 호출.
        탐색 시도·서브콜처럼 다른 재료로 부르는 호출은 명시해서 넘긴다.
        """
        use_stream = self.stream if stream is None else bool(stream)
        kwargs = self._kwargs(state, model_config, purpose, extra_messages, messages, system, tools)
        state.add_event(
            "api.request",
            {
                "model": getattr(model_config, "model", ""),
                "provider": self.provider,
                "message_count": len(kwargs["messages"]),
                "has_tools": bool(kwargs.get("tools")),
                "has_thinking": bool(getattr(model_config, "thinking_enabled", False))
                or bool(getattr(model_config, "thinking_level", None)),
                "stream": use_stream,
                "purpose": purpose,
            },
        )
        t_request = time.monotonic()
        state.shared["_api_call_t0"] = t_request
        state.shared.pop("_api_ttft_emitted", None)
        try:
            if use_stream:
                response = await self._streaming_with_retry(state, model_config, kwargs, emit_text)
            else:
                response = await self._plain_with_retry(state, kwargs)
                state.add_event(
                    "api.ttft",
                    {
                        "ttft_ms": round((time.monotonic() - t_request) * 1000.0, 1),
                        "provider": self.provider,
                        "model": getattr(model_config, "model", ""),
                        "stream": False,
                        "iteration": getattr(state, "iteration", 0),
                        "first_visible": "complete",
                    },
                )
        except APIError as e:
            payload: Dict[str, Any] = {
                "code": e.code.value if e.code is not None else "exec.unknown",
                "category": e.category.value,
                "provider": self.provider,
                "message": str(e),
            }
            cli_version = getattr(self.client, "_cli_version_value", None)
            if cli_version:
                payload["cli_version"] = str(cli_version)
            state.add_event("api.error", payload)
            raise
        state.add_event(
            "api.response",
            {
                "stop_reason": response.stop_reason,
                "text_length": len(response.text),
                "tool_calls": len(response.tool_calls),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
                "purpose": purpose,
            },
        )
        return response

    # ── 요청 조립 ───────────────────────────────────────────────────────
    def _kwargs(
        self,
        state: Any,
        model_config: Any,
        purpose: str,
        extra_messages: Optional[List[Dict[str, Any]]],
        messages: Optional[List[Dict[str, Any]]],
        system: Any,
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        if messages is None:
            msgs = list(state.messages)
            retired = state.shared.get(SharedKeys.RETIRED_TOOL_CALLS)
            if retired:
                msgs = retire_tool_calls_by_name(msgs, retired)
            turn_context = state.shared.get("turn_context_text")
            if isinstance(turn_context, str) and turn_context:
                msgs = inject_turn_context(msgs, turn_context)
        else:
            msgs = list(messages)
        if extra_messages:
            msgs.extend(extra_messages)
        kwargs: Dict[str, Any] = {
            "model_config": model_config,
            "messages": normalize_messages_for_request(msgs),
            "purpose": purpose,
        }
        sys_value = state.system if system is None else system
        if sys_value:
            kwargs["system"] = sys_value
        tools_value = state.tools if tools is None else tools
        if tools_value:
            kwargs["tools"] = tools_value
        if getattr(state, "tool_choice", None) and tools is None:
            kwargs["tool_choice"] = state.tool_choice
        return kwargs

    def _first_chunk_timeout_s(self) -> float:
        if self._timeout_ms:
            return max(0.001, self._timeout_ms / 1000.0)
        return _timeouts.first_chunk_timeout_s()

    def _request_timeout_s(self) -> float:
        if self._timeout_ms:
            return max(0.001, self._timeout_ms / 1000.0)
        return _timeouts.request_timeout_s()

    @staticmethod
    def _timeout_budget_spent(category: ErrorCategory, timeouts_so_far: int) -> bool:
        return category == ErrorCategory.TIMEOUT and timeouts_so_far > _timeouts.timeout_retries()

    # ── 재시도 ──────────────────────────────────────────────────────────
    async def _plain_with_retry(self, state: Any, kwargs: Dict[str, Any]) -> Any:
        last_error: Optional[Exception] = None
        timeouts_seen = 0
        for attempt in range(self._retry.max_retries + 1):
            try:
                limit = self._request_timeout_s()
                try:
                    return await asyncio.wait_for(self.client.create_message(**kwargs), timeout=limit)
                except asyncio.TimeoutError:
                    raise APIError(
                        f"모델이 {limit:g}초 안에 답하지 않았습니다",
                        category=ErrorCategory.TIMEOUT,
                        code=ExecutorErrorCode.EXEC_API_TIMEOUT,
                    ) from None
            except APIError as e:
                last_error = e
                if e.category == ErrorCategory.TIMEOUT:
                    timeouts_seen += 1
                if self._timeout_budget_spent(e.category, timeouts_seen):
                    raise
                if not self._retry.should_retry(e.category, attempt):
                    raise
                delay = self._retry.get_delay(attempt)
                state.add_event(
                    "api.retry",
                    {"attempt": attempt + 1, "category": e.category.value, "code": e.code.value, "delay": delay},
                )
                await asyncio.sleep(delay)
            except Exception as e:  # noqa: BLE001
                last_error = e
                category = ErrorCategory.UNKNOWN
                if not self._retry.should_retry(category, attempt):
                    raise APIError(str(e), category=category, cause=e) from e
                delay = self._retry.get_delay(attempt)
                state.add_event(
                    "api.retry",
                    {
                        "attempt": attempt + 1,
                        "category": category.value,
                        "code": ExecutorErrorCode.from_category(category).value,
                        "delay": delay,
                    },
                )
                await asyncio.sleep(delay)
        raise last_error or APIError(
            "Max retries exceeded",
            category=ErrorCategory.UNKNOWN,
            code=ExecutorErrorCode.EXEC_API_RETRY_EXHAUSTED,
        )

    async def _streaming_with_retry(
        self, state: Any, model_config: Any, kwargs: Dict[str, Any], emit_text: bool
    ) -> Any:
        last_error: Optional[Exception] = None
        timeouts_seen = 0
        for attempt in range(self._retry.max_retries + 1):
            try:
                return await self._streaming(state, model_config, kwargs, emit_text)
            except APIError as e:
                last_error = e
                if e.category == ErrorCategory.TIMEOUT:
                    timeouts_seen += 1
                if self._timeout_budget_spent(e.category, timeouts_seen):
                    raise
                if not self._retry.should_retry(e.category, attempt):
                    raise
                delay = self._retry.get_delay(attempt)
                self._signal_stream_restart(state)
                state.add_event(
                    "api.retry",
                    {"attempt": attempt + 1, "category": e.category.value, "delay": delay, "stream": True},
                )
                await asyncio.sleep(delay)
            except Exception as e:  # noqa: BLE001
                last_error = e
                category = ErrorCategory.UNKNOWN
                if not self._retry.should_retry(category, attempt):
                    raise APIError(str(e), category=category, cause=e) from e
                delay = self._retry.get_delay(attempt)
                self._signal_stream_restart(state)
                state.add_event(
                    "api.retry",
                    {"attempt": attempt + 1, "category": category.value, "delay": delay, "stream": True},
                )
                await asyncio.sleep(delay)
        raise last_error or APIError(
            "Max retries exceeded",
            category=ErrorCategory.UNKNOWN,
            code=ExecutorErrorCode.EXEC_API_RETRY_EXHAUSTED,
        )

    @staticmethod
    def _signal_stream_restart(state: Any) -> None:
        """스트림 재시도 직전 — 이미 흘린 글을 버리라고 알린다(같은 글이 두 번 보이지 않게)."""
        if state.shared.get("_api_ttft_emitted"):
            state.shared.pop("_api_ttft_emitted", None)
            state.add_event("api.stream_restart", {})

    async def _streaming(self, state: Any, model_config: Any, kwargs: Dict[str, Any], emit_text: bool) -> Any:
        response = None
        source = "cli" if self.is_subprocess else "api"
        t_anchor = state.shared.get("_api_call_t0") or time.monotonic()
        stream: AsyncIterator[Dict[str, Any]] = self.client.create_message_stream(**kwargs)
        if source != "cli":
            # CLI 백엔드는 스트림 안에서 도구를 직접 실행하므로(몇 분짜리 Bash 도 정상) 감시하지 않는다.
            stream = watched_stream(
                stream,
                first_chunk_s=self._first_chunk_timeout_s(),
                idle_s=_timeouts.idle_timeout_s(),
            )
        granularity = str(
            getattr(getattr(self.client, "capabilities", None), "streaming_granularity", "token") or "token"
        )
        async for chunk in stream:
            chunk_type = chunk.get("type")
            if chunk_type in _CONTENT_CHUNK_TYPES and not state.shared.get("_api_ttft_emitted"):
                state.shared["_api_ttft_emitted"] = True
                state.add_event(
                    "api.ttft",
                    {
                        "ttft_ms": round((time.monotonic() - t_anchor) * 1000.0, 1),
                        "provider": self.provider,
                        "model": getattr(model_config, "model", ""),
                        "stream": True,
                        "iteration": getattr(state, "iteration", 0),
                        "first_visible": chunk_type,
                    },
                )
            if chunk_type == "message_complete":
                response = chunk["response"]
            elif chunk_type == "text_delta" and chunk.get("text"):
                if emit_text:
                    state.add_event(
                        "text.delta",
                        {"text": chunk["text"], "source": source, "granularity": granularity},
                    )
            elif chunk_type == "thinking_delta" and chunk.get("text"):
                state.add_event("thinking.delta", {"text": chunk["text"]})
            elif chunk_type == "tool_use":
                payload = {
                    "id": chunk.get("id"),
                    "name": chunk.get("name"),
                    "input": chunk.get("input") or {},
                    "source": source,
                }
                state.add_event("api.tool_use", payload)
                if source == "cli":
                    state.add_event("api.cli_tool_call", dict(payload))
            elif chunk_type == "input_json_delta":
                state.add_event("api.input_json_delta", {"delta": chunk.get("delta", "")})
            elif chunk_type == "content_block_stop":
                state.add_event("api.content_block_stop", {})
            elif chunk_type == "tool_result":
                state.add_event(
                    "api.tool_result",
                    {
                        "tool_use_id": chunk.get("tool_use_id", ""),
                        "content": chunk.get("content"),
                        "is_error": bool(chunk.get("is_error", False)),
                        "source": source,
                    },
                )
        if response is None:
            raise APIError(
                "Stream ended without message_complete",
                category=ErrorCategory.NETWORK,
                code=ExecutorErrorCode.EXEC_API_STREAM_INCOMPLETE,
            )
        return response
