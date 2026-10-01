"""RSI 실행 코어 — :class:`xgen_rsi.turn_executor.GenyRSITurnExecutor` 가 턴 조립 뒤에 부른다.

턴 조립(호스트 계약 26단계)은 :func:`xgen_rsi.assembly.assemble_turn` 이 끝낸 상태로 :class:`TurnPlan` 이 들어온다.
여기서는 실행 코어만 짓는다: 하네스 해석(lineage) → 구성요소 인스턴스 → 원장 클라이언트 → 엔진 → 동기 다리.

관리자 설정(``host.setting``):

* ``XGEN_RSI_HARNESS_DIR`` — 하네스 디렉터리 하나로 고정(기본: 내장 H0)
* ``XGEN_RSI_LINEAGE_FILE`` — 정책 계열 → 하네스 디렉터리 표(JSON ``{"lineages": {...}}``)
* ``XGEN_RSI_RECORD_DIR`` — 궤적 기록 위치(없으면 기록하지 않음)
* ``XGEN_RSI_RECORD_CONTENT`` — 기록에 전사·최종 글까지 남김(평가 실행용, 운영 기본 끔)
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from xgen_rsi.harness.runtime import LoadedHarness, TurnRuntime, instantiate
from xgen_rsi.harness.spec import HarnessManifest, LineageTable, load_manifest
from xgen_rsi.kernel.engine import TurnEngine
from xgen_rsi.kernel.events import EventHub
from xgen_rsi.kernel.ledger import LedgerClient, UsageLedger
from xgen_rsi.kernel.model_call import ModelCaller
from xgen_rsi.kernel.recorder import TrajectoryRecorder
from xgen_rsi.kernel.stream import TurnDriver
from xgen_rsi.kernel.tools import ToolRunner

logger = logging.getLogger(__name__)

BUILTIN_H0 = Path(__file__).resolve().parent.parent / "harnesses" / "h0"

_CACHE_LOCK = threading.Lock()
_MANIFEST_CACHE: Dict[Tuple[str, float], Tuple[HarnessManifest, str]] = {}


def _setting(host: Any, key: str, default: str = "") -> str:
    getter = getattr(host, "setting", None)
    if not callable(getter):
        return default
    try:
        value = getter(key)
    except Exception:  # noqa: BLE001
        return default
    return str(value) if value not in (None, "") else default


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def load_cached(root: os.PathLike[str] | str) -> Tuple[HarnessManifest, str]:
    """manifest 를 읽어 (manifest, version_id) — 파일 변경 시각으로 캐시한다."""
    path = Path(root).resolve()
    stamp = (path / "manifest.json").stat().st_mtime
    key = (str(path), stamp)
    with _CACHE_LOCK:
        hit = _MANIFEST_CACHE.get(key)
        if hit is not None:
            return hit
    manifest = load_manifest(path)
    entry = (manifest, manifest.version_id())
    with _CACHE_LOCK:
        _MANIFEST_CACHE[key] = entry
    return entry


def resolve_harness_dir(host: Any, provider: str, model: str) -> Tuple[Path, str]:
    """(하네스 디렉터리, 계보 이름)."""
    fixed = _setting(host, "XGEN_RSI_HARNESS_DIR")
    if fixed:
        return Path(fixed), "fixed"
    lineage_file = _setting(host, "XGEN_RSI_LINEAGE_FILE")
    if lineage_file:
        table = LineageTable.load(lineage_file)
        target = table.resolve(provider, model)
        base = Path(lineage_file).resolve().parent
        path = Path(target)
        return (path if path.is_absolute() else base / path), target
    return BUILTIN_H0, "builtin:h0"


@dataclass
class PreparedTurn:
    engine: TurnEngine
    rt: TurnRuntime
    hub: EventHub
    ledger: UsageLedger
    recorder: Optional[TrajectoryRecorder]
    provider_label: str
    plan: Any
    _rollout: Any = None

    # ── rollout(관리자 옵트인, 기존 형식) ─────────────────────────────────
    def open_rollout(self, loop: Any) -> None:
        path = getattr(self.plan, "rollout_path", None)
        if path is None:
            return
        from xgen_agent_runtime.core.rollout_recorder import RolloutRecorder

        async def _make() -> Any:
            # 기록기는 실행 중인 이벤트 루프 안에서 만들어야 한다(자기 쓰기 태스크를 그 루프에 건다).
            return RolloutRecorder(path)

        recorder = loop.run_until_complete(_make())
        self._rollout = recorder

        def _tap(event: Any) -> None:
            try:
                recorder.record_nowait(event)
            except Exception:  # noqa: BLE001
                logger.debug("rsi: rollout record failed", exc_info=True)

        self.hub.subscribe(_tap)

    def close_rollout(self, loop: Any) -> None:
        if self._rollout is None:
            return
        try:
            loop.run_until_complete(self._rollout.shutdown())
        except Exception:  # noqa: BLE001
            logger.error("rsi: rollout recorder shutdown failed", exc_info=True)
        try:
            from xgen_agent_runtime.host.rollouts import ROLLOUT_KEEP_LAST, prune_rollout_files

            path = os.fspath(self.plan.rollout_path)
            loop.run_until_complete(asyncio.to_thread(prune_rollout_files, os.path.dirname(path), keep_last=ROLLOUT_KEEP_LAST))
        except Exception:  # noqa: BLE001
            logger.debug("rsi: rollout retention failed", exc_info=True)
        self._rollout = None

    def output_settle(self, text: str, schema: Dict[str, Any]) -> str:
        return self.engine.output.settle(text, schema)

    # ── 턴 끝: 실행 기록 · 궤적 기록 · 기억 닫기 · 증류 ─────────────────────
    def finish_turn(
        self,
        loop: Any,
        *,
        host: Any,
        input_text: Any,
        output_text: str,
        success: bool,
        duration_ms: int,
        error: str,
        cancelled: bool,
        produced_output: bool,
        unfinished: str = "",
    ) -> None:
        """``unfinished`` 가 ``"cancelled"``/``"error"`` 면 끝나지 못한 턴의 대화를 단기 기억에 먼저 남긴다
        (기존 엔진 4.77.0 과 같은 함수·같은 표식 — 다음 턴이 무엇을 하다 멈췄는지 안다)."""
        state = self.plan.state
        if self.recorder is not None:
            try:
                from xgen_agent_runtime.host.harness_components import harness_summary

                summary = harness_summary(state) or {}
                keep = getattr(self.recorder, "_keep_content", False)
                self.recorder.finish(
                    status=str(state.run_status),
                    termination_reason=str(state.termination_reason or ""),
                    final_text=output_text,
                    ledger=self.ledger,
                    components_fired=summary.get("components"),
                    error=error or None,
                    transcript=list(state.messages) if keep else None,
                )
            except Exception:  # noqa: BLE001
                logger.warning("rsi: trajectory finish failed", exc_info=True)
        provider = self.plan.memory_provider
        if unfinished and provider is not None:
            from types import SimpleNamespace

            from xgen_agent_runtime.host.runner import _record_unfinished_turn

            _record_unfinished_turn(
                SimpleNamespace(_memory_provider=provider),  # type: ignore[arg-type]
                loop,
                state=state,
                input_text=input_text,
                reason=unfinished,
            )
        record_ok = produced_output or success or bool(getattr(host, "record_failed_starts", True))
        if provider is not None and record_ok:
            calls, failures, blocked = _tool_stats(state)
            spec = self.plan.memory_distill_spec
            try:
                from xgen_agent_runtime.host.execution_record import record_turn_execution

                text = input_text if isinstance(input_text, str) else str((input_text or {}).get("text", "") if isinstance(input_text, dict) else input_text)
                loop.run_until_complete(
                    asyncio.wait_for(
                        record_turn_execution(
                            provider,
                            input_text=text,
                            output_text=output_text,
                            success=success,
                            duration_ms=duration_ms,
                            session_id=str(getattr(state, "session_id", "") or ""),
                            provider_name=str(getattr(spec, "provider", "") or "") if spec else "",
                            model=str(getattr(spec, "model", "") or "") if spec else "",
                            error=error,
                            cancelled=cancelled,
                            tool_calls=calls,
                            tool_failures=failures,
                            blocked=blocked,
                        ),
                        timeout=10.0,
                    )
                )
            except Exception:  # noqa: BLE001
                logger.debug("rsi: execution record failed (turn unaffected)", exc_info=True)
        if provider is not None:
            try:
                loop.run_until_complete(provider.close())
            except Exception:  # noqa: BLE001
                logger.debug("rsi: memory provider close failed", exc_info=True)
            spec = self.plan.memory_distill_spec
            if spec is not None:
                try:
                    from xgen_agent_runtime.host.distill import launch_distillation

                    launch_distillation(spec)
                except Exception:  # noqa: BLE001
                    logger.debug("rsi: distillation launch failed", exc_info=True)
        try:
            loop.run_until_complete(_aclose(self.engine.caller.client))
        except Exception:  # noqa: BLE001
            pass


async def _aclose(client: Any) -> None:
    inner = getattr(client, "inner", client)
    aclose = getattr(inner, "aclose", None)
    if aclose is not None:
        await aclose()


def _tool_stats(state: Any) -> Tuple[int, int, int]:
    """이 턴(모든 슬라이스)의 도구 호출·실패·반복 차단 수."""
    counts = getattr(state, "_turn_event_counts", None) or {}
    calls = failures = blocked = 0
    for ev in list(getattr(state, "events", None) or []):
        if not isinstance(ev, dict):
            continue
        etype = str(ev.get("type") or "")
        data = ev.get("data") or {}
        if etype == "tool.execute_complete":
            calls += int(data.get("count") or 0)
            failures += int(data.get("errors") or 0)
        elif etype == "tool.repeat_blocked":
            blocked += len(data.get("tools") or []) or 1
    if not calls and counts.get("tool.execute_complete"):
        calls = int(counts.get("tool.execute_complete") or 0)
    return calls, failures, blocked


class RSITurnExecutor:
    """턴 조립이 끝난 :class:`TurnPlan` 으로 도는 RSI 실행 코어(``prepare`` → ``execute``)."""

    def prepare(self, plan: Any, host: Any) -> PreparedTurn:
        from xgen_agent_runtime.core.config import ModelConfig
        from xgen_agent_runtime.host.runner import build_client

        harness_dir, lineage = resolve_harness_dir(host, plan.provider, plan.model)
        manifest, version = load_cached(harness_dir)
        harness: LoadedHarness = instantiate(manifest, version_id=version)

        kw = plan.pipeline_kwargs
        factory = getattr(host, "rsi_client_factory", None)  # RSI 전용 선택 훅(평가·재현) — 운영 호스트엔 없다
        if plan.llm_client is not None:
            client = plan.llm_client
        elif callable(factory):
            client = factory(plan)
        elif plan.credentials and any(v not in (None, "") for v in plan.credentials.values()):
            client = build_client(plan.provider, plan.api_key, plan.base_url, credentials=plan.credentials)
        else:
            client = build_client(plan.provider, plan.api_key, plan.base_url)
        ledger = UsageLedger(provider=str(getattr(client, "provider", "") or plan.provider))
        lclient = LedgerClient(client, ledger)

        state = plan.state
        hub = EventHub(session_id=str(getattr(state, "session_id", "") or ""))
        hub.attach_state(state)
        # 턴 기록 필드(기존 PipelineConfig.apply_to_state 가 쓰던 값)
        model_config = ModelConfig(
            model=plan.model,
            max_tokens=int(kw.get("max_tokens", 8192)),
            temperature=float(kw.get("temperature", 0.7)),
            thinking_level=kw.get("thinking_level"),
        )
        state.model = model_config.model
        state.max_tokens = model_config.max_tokens
        state.temperature = model_config.temperature
        state.thinking_level = model_config.thinking_level
        state.max_iterations = int(kw.get("max_iterations", 20))
        state.context_window_budget = int(kw.get("context_window_budget") or 200_000)
        state.stream = bool(kw.get("stream", True))
        state.llm_client = lclient
        if getattr(state, "credentials", None) is None:
            state.credentials = None

        record_dir = _setting(host, "XGEN_RSI_RECORD_DIR")
        recorder: Optional[TrajectoryRecorder] = None
        if record_dir:
            recorder = TrajectoryRecorder(
                task_id=str(getattr(plan, "interaction_id", "") or ""),
                harness_id=version,
                harness_name=manifest.name,
                lineage=lineage,
                provider=str(getattr(client, "provider", "") or plan.provider),
                model=plan.model,
                thinking_level=model_config.thinking_level,
                explore_policy_id=(manifest.exploration_policy or {}).get("id") if manifest.exploration_policy else None,
                sink_dir=record_dir,
                keep_content=_truthy(_setting(host, "XGEN_RSI_RECORD_CONTENT")),
            )
            hub.subscribe(recorder.on_event)

        rt = TurnRuntime(
            plan=plan,
            state=state,
            harness=harness,
            gateway=None,  # type: ignore[arg-type]
            ledger=ledger,
            emit=lambda t, d: state.add_event(t, d),
            model_config=model_config,
            registry=None if plan.is_cli else plan.registry,
            memory_provider=plan.memory_provider,
            is_cli=bool(plan.is_cli),
            agent_settings={k: kw.get(k) for k in ("repeat_stop_after", "prune_over_tokens", "turn_input_budget_tokens") if k in kw},
        )

        # 하네스가 기여하는 도구(구조 레버: skill 의 ReadSkill 등) — 호스트 정책이 끝난 레지스트리에 더한다.
        contributed = []
        for comp in harness.components.values():
            hook = getattr(comp, "tools", None)
            if callable(hook):
                contributed.extend(hook(rt) or [])
        registry = plan.registry
        if contributed:
            if registry is None and not plan.is_cli:
                from xgen_agent_runtime.tools import ToolRegistry

                registry = ToolRegistry()
                rt.registry = registry
            if registry is not None:
                for tool in contributed:
                    if registry.get(tool.name) is None:
                        registry.register(tool, core=True)

        tools: Optional[ToolRunner] = None
        if not plan.is_cli and registry is not None and len(registry):
            from xgen_agent_runtime.host.runner import ensure_surface_entrances

            ensure_surface_entrances(registry)
            tool_policy = harness.maybe("client_tool")
            tools = ToolRunner(
                registry,
                tool_context=plan.run_tool_context,
                result_filter=plan.result_filter,
                executor=str(tool_policy.param("executor", "sequential")) if tool_policy is not None else "sequential",
                max_concurrency=int(tool_policy.param("max_concurrency", 10)) if tool_policy is not None else 10,
            )
        rt.tool_context_provider = (lambda: tools.context) if tools is not None else (lambda: plan.run_tool_context)

        caller = ModelCaller(lclient, stream=bool(kw.get("stream", True)))
        rt.gateway = caller
        engine = TurnEngine(rt, caller, tools, recorder)
        return PreparedTurn(
            engine=engine,
            rt=rt,
            hub=hub,
            ledger=ledger,
            recorder=recorder,
            provider_label=str(getattr(client, "provider", "") or ""),
            plan=plan,
        )

    def execute(self, prepared: PreparedTurn, plan: Any, host: Any) -> Any:
        driver = TurnDriver(prepared, plan, host)
        if plan.streaming:
            turn_iter = driver.stream()
            if plan.clamped and plan.schema is None:
                from xgen_agent_runtime.host.context_budget import CLAMP_NOTICE

                def _with_notice(inner: Any) -> Any:
                    yield CLAMP_NOTICE
                    yield from inner

                return _with_notice(turn_iter)
            return turn_iter
        try:
            return driver.run()
        finally:
            plan.teardown()
