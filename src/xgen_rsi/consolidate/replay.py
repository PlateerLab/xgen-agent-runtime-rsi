"""재생 — 기록된 턴 세계 위에서 하네스 하나로 그 턴을 다시 돈다(Dream-RSI 의 replay, 설계 41 §2).

* 턴 계획은 기록에서 다시 만든다: 시스템 조각·출력 스키마·노드 손잡이·앞 대화·입력·조립이 상태에 남긴 값.
  하네스가 짓는 것(시스템 프롬프트 완성본·노출 도구·압축)은 **재생하는 하네스가** 다시 짓는다.
* 도구는 기록된 결과를 돌려준다(:class:`CallBook`). 실제 도구는 돌지 않는다 — 부작용도, 외부 시스템 접근도, 도구 지연도 없다.
  레지스트리만 읽는 메타 도구(ToolSearch 등)는 재생 레지스트리 위에서 실제로 돈다.
* 기록에 없는 호출 → Child = ∅: "기록된 결과 없음"을 돌려주고 재생은 계속된다. 지원 밖 호출 수를 센다.
* 기억은 기록된 검색 결과를 돌려주고, 기억에 쓰지 않는다.
* 모델 응답만 새로 받는다 — 정책 π(에이전트의 모델, XGEN 에 등록된 자격증명).

진입점: :func:`replay_world`.
"""

from __future__ import annotations

import copy
import json
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from xgen_rsi.base.tools.base import Tool, ToolContext, ToolResult
from xgen_rsi.consolidate.world import TurnWorld
from xgen_rsi.host import LocalHost

NO_RECORD = ("[replay] No recorded result exists for this call: it was not made in the recorded session, "
             "so its outcome is unknown.")


def _canon(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _loose(value: Any) -> Any:
    """느슨한 비교용 — 글은 공백을 하나로, 대소문자를 무시한다."""
    if isinstance(value, str):
        return " ".join(value.split()).lower()
    if isinstance(value, dict):
        return {str(k): _loose(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_loose(v) for v in value]
    return value


class CallBook:
    """기록된 도구 호출 → 결과. 같은 (이름, 인자)가 여러 번 기록됐으면 순서대로, 다 쓰면 마지막 결과를 다시 준다."""

    def __init__(self, calls: List[Dict[str, Any]]) -> None:
        self._exact: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self._loose: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self._by_name: Dict[str, List[Dict[str, Any]]] = {}
        for call in calls or ():
            name = str(call.get("name") or "")
            inp = call.get("input") or {}
            self._exact.setdefault((name, _canon(inp)), []).append(call)
            self._loose.setdefault((name, _canon(_loose(inp))), []).append(call)
            self._by_name.setdefault(name, []).append(call)
        self._used: Dict[Tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self.answered = 0
        self.off_support: List[Dict[str, Any]] = []

    def _take(self, table: Dict[Tuple[str, str], List[Dict[str, Any]]], key: Tuple[str, str], tag: str) -> Optional[Dict[str, Any]]:
        hits = table.get(key)
        if not hits:
            return None
        n = self._used.get((tag,) + key, 0)  # type: ignore[arg-type]
        self._used[(tag,) + key] = n + 1  # type: ignore[index]
        return hits[min(n, len(hits) - 1)]

    def answer(self, name: str, tool_input: Any) -> Tuple[Any, bool, bool]:
        """(content, is_error, supported)."""
        with self._lock:
            hit = self._take(self._exact, (name, _canon(tool_input or {})), "e")
            if hit is None:
                hit = self._take(self._loose, (name, _canon(_loose(tool_input or {}))), "l")
            if hit is None:
                from xgen_rsi.base.tools.gates import gate_of

                if gate_of(name) is not None and self._by_name.get(name):
                    hit = self._by_name[name][-1]  # 문(안내 도구)의 글은 인자와 무관하다
            if hit is None:
                self.off_support.append({"name": name, "input": tool_input})
                return NO_RECORD, True, False
            self.answered += 1
            return copy.deepcopy(hit.get("content")), bool(hit.get("is_error")), True


class ReplayTool(Tool):
    """기록된 정의(이름·설명·스키마)를 가진 도구. 실행 = 기록된 결과."""

    def __init__(self, definition: Dict[str, Any], book: CallBook) -> None:
        self._def = definition
        self._book = book

    @property
    def name(self) -> str:  # type: ignore[override]
        return str(self._def["name"])

    @property
    def description(self) -> str:  # type: ignore[override]
        return str(self._def.get("description") or "")

    @property
    def input_schema(self) -> Dict[str, Any]:  # type: ignore[override]
        return dict(self._def.get("input_schema") or {"type": "object", "properties": {}})

    async def execute(self, input: Dict[str, Any], context: ToolContext) -> ToolResult:  # noqa: A002 — Tool ABI
        content, is_error, _ = self._book.answer(self.name, input)
        return ToolResult(content=content, is_error=is_error)


class _ReplayRetriever:
    def __init__(self, chunks: Optional[List[Dict[str, Any]]]) -> None:
        self._chunks = chunks

    async def retrieve(self, query: str, state: Any) -> List[Any]:
        from xgen_rsi.base.stages.s02_context.types import MemoryChunk

        return [MemoryChunk(key=c.get("key", ""), content=c.get("content", ""), source=c.get("source", ""),
                            relevance_score=float(c.get("relevance_score") or 0.0), metadata=dict(c.get("metadata") or {}))
                for c in (self._chunks or [])]


class ReplayMemoryProvider:
    """기록된 기억 검색 결과를 돌려주는 기억 자리. 쓰지 않는다(커널·기억 구성요소가 ``rsi_replay`` 를 본다)."""

    rsi_replay = True

    def __init__(self, chunks: Optional[List[Dict[str, Any]]]) -> None:
        self._chunks = chunks

    def rsi_retriever(self) -> _ReplayRetriever:
        return _ReplayRetriever(self._chunks)

    async def close(self) -> None:
        return None


def build_registry(world: TurnWorld, book: CallBook) -> Any:
    """기록된 도구 목록 → 재생 레지스트리(같은 이름·설명·스키마·노출 상태). 하네스가 기여한 도구는 빼 둔다(재생 하네스가 다시 기여)."""
    from xgen_rsi.base.tools import ToolRegistry
    from xgen_rsi.base.tools.built_in import BUILT_IN_TOOL_CLASSES

    tools = (world.data.get("tools") or {})
    contributed = set(tools.get("contributed") or [])
    registry = ToolRegistry()
    for d in tools.get("defs") or []:
        name = str(d.get("name") or "")
        if not name or name in contributed:
            continue
        live_cls = BUILT_IN_TOOL_CLASSES.get(name) if d.get("live") else None
        tool = live_cls() if live_cls is not None else ReplayTool(d, book)
        registry.register(tool, core=bool(d.get("core")))
        if d.get("activated"):
            registry.activate(name)
    return registry


def build_plan(world: TurnWorld, *, provider: str, model: str, api_key: str, base_url: Optional[str],
               credentials: Any, registry: Any, memory_provider: Any, workdir: str,
               cancelled: Callable[[], bool]) -> Any:
    """기록 → :class:`~xgen_rsi.assembly.TurnPlan` (모델·자격증명은 정책 π 의 것)."""
    from xgen_rsi.assembly import TurnPlan
    from xgen_rsi.base import PipelineState

    d = world.data
    p = dict(d.get("plan") or {})
    st = dict(d.get("state") or {})
    state = PipelineState(session_id=str(st.get("session_id") or ""))
    state.messages = copy.deepcopy(list(st.get("messages") or []))
    state.metadata.update(copy.deepcopy(dict(st.get("metadata") or {})))
    state.shared.update(copy.deepcopy(dict(st.get("shared") or {})))
    kw = copy.deepcopy(dict(p.get("pipeline_kwargs") or {}))
    if isinstance(kw.get("turn_input_budget_tokens"), list):
        kw["turn_input_budget_tokens"] = tuple(kw["turn_input_budget_tokens"])
    kw.update(provider=provider, model=model, stream=False)
    schema = p.get("schema") or None
    pipeline_input = copy.deepcopy(d.get("input"))
    kwargs = dict(p.get("kwargs") or {})
    kwargs.update(tool_events=False, streaming=False)
    return TurnPlan(
        node_name=str(p.get("node_name") or ""),
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        credentials=credentials,
        schema=schema,
        registry=registry,
        result_sink={},
        state=state,
        system_prompt=str(p.get("system_prompt") or ""),
        system_parts=[(str(a), str(b)) for a, b in (p.get("system_parts") or [])],
        is_cli=False,
        tool_surface=None,
        llm_client=None,
        memory_provider=memory_provider,
        memory_distill_spec=None,
        run_tool_context=ToolContext(working_dir=workdir, allowed_paths=[workdir]),
        result_filter=None,
        budget_window=int(p.get("budget_window") or 0),
        enable_compaction=bool(p.get("enable_compaction", True)),
        max_tokens=int(p.get("max_tokens") or kw.get("max_tokens") or 8192),
        streaming=False,
        clamped=False,
        pipeline_input=pipeline_input,
        rollout_path=None,
        pipeline_kwargs=kw,
        kwargs=kwargs,
        interaction_id=str(d.get("interaction_id") or ""),
        response_io_id=None,
        cancelled=cancelled,
        teardown=lambda: None,
    )


@dataclass
class ReplayResult:
    """재생 한 번의 결과."""

    world_id: str
    answer: str = ""
    status: str = ""
    termination_reason: str = ""
    policy_tokens: int = 0
    steps: Dict[str, int] = field(default_factory=dict)
    params_read: List[str] = field(default_factory=list)
    off_support: int = 0
    answered: int = 0
    transcript: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    seconds: float = 0.0

    @property
    def missing(self) -> bool:
        """인프라 실패(모델 호출 실패 등) — RRSI 의 누락 시행(r = 0, 분모 포함)."""
        return bool(self.error)

    def to_json(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in ("world_id", "answer", "status", "termination_reason", "policy_tokens", "steps",
                                               "params_read", "off_support", "answered", "error", "seconds")}

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> "ReplayResult":
        return cls(**{k: raw[k] for k in ("world_id", "answer", "status", "termination_reason", "policy_tokens", "steps",
                                          "params_read", "off_support", "answered", "error", "seconds") if k in raw})


class _ReplayHost(LocalHost):
    """재생 턴의 호스트 — 하네스를 고정하고 궤적 요약을 받는다."""

    def __init__(self, *, harness_dir: str, policy: Any, client_factory: Optional[Callable[[Any], Any]], workdir: str) -> None:
        super().__init__(provider=policy.provider, model=policy.model, api_key=policy.api_key or "",
                         base_url=policy.base_url, credentials=policy.credentials, workspace=workdir,
                         settings={"XGEN_RSI_HARNESS_DIR": harness_dir}, client_factory=client_factory)
        self.record: Optional[Dict[str, Any]] = None

    def rsi_record(self, record: Dict[str, Any]) -> None:
        self.record = record


def replay_world(world: TurnWorld, harness_dir: str | Path, policy: Any, *,
                 client_factory: Optional[Callable[[Any], Any]] = None,
                 cancelled: Optional[Callable[[], bool]] = None) -> ReplayResult:
    """``world`` 를 ``harness_dir`` 하네스로 재생한다. 예외 대신 ``error`` 가 찬 결과를 돌려준다."""
    from xgen_rsi.kernel.executor import RSITurnExecutor

    started = time.monotonic()
    out = ReplayResult(world_id=world.id)
    if not world.replayable:
        out.error = "world is not replayable"
        return out
    book = CallBook(world.tool_calls)
    mem = world.data.get("memory") or {}
    memory_provider = ReplayMemoryProvider(mem.get("chunks")) if mem.get("present") else None
    with tempfile.TemporaryDirectory(prefix="rsi-replay-") as workdir:
        host = _ReplayHost(harness_dir=str(harness_dir), policy=policy, client_factory=client_factory, workdir=workdir)
        try:
            registry = build_registry(world, book)
            plan = build_plan(world, provider=policy.provider, model=policy.model, api_key=policy.api_key or "",
                              base_url=policy.base_url, credentials=policy.credentials, registry=registry,
                              memory_provider=memory_provider, workdir=workdir, cancelled=cancelled or (lambda: False))
            if getattr(policy, "temperature", None) is not None and "temperature" not in plan.pipeline_kwargs:
                plan.pipeline_kwargs["temperature"] = policy.temperature
            executor = RSITurnExecutor()
            prepared = executor.prepare(plan, host)
            answer = executor.execute(prepared, plan, host)
            out.answer = str(answer or "")
            state = plan.state
            out.status = str(getattr(state, "run_status", "") or "")
            out.termination_reason = str(getattr(state, "termination_reason", "") or "")
            rec = host.record or {}
            out.policy_tokens = int(rec.get("policy_tokens") or prepared.ledger.policy_tokens() or 0)
            out.steps = dict(rec.get("steps") or {})
            out.params_read = list(rec.get("params_read") or [])
            history_len = len((world.data.get("state") or {}).get("messages") or [])
            out.transcript = _transcript(list(getattr(state, "messages", None) or [])[history_len:])
            if out.answer.startswith("[ERROR]") and out.status not in ("completed",):
                out.error = out.answer[:500]
        except Exception as exc:  # noqa: BLE001 — 재생 실패는 결과로(누락 시행)
            out.error = f"{type(exc).__name__}: {exc}"[:500]
    out.off_support = len(book.off_support)
    out.answered = book.answered
    out.seconds = round(time.monotonic() - started, 3)
    return out


_TRANSCRIPT_CLIP = 4000


def _transcript(messages: List[Any]) -> List[Dict[str, Any]]:
    def clip(v: Any) -> Any:
        if isinstance(v, str):
            return v if len(v) <= _TRANSCRIPT_CLIP else v[:_TRANSCRIPT_CLIP] + "…"
        if isinstance(v, list):
            return [clip(x) for x in v]
        if isinstance(v, dict):
            if v.get("type") in ("image", "input_image"):
                return {"type": "text", "text": "[image]"}
            return {k: clip(x) for k, x in v.items()}
        try:
            json.dumps(v)
            return v
        except (TypeError, ValueError):
            return str(v)

    return [{"role": str(m.get("role") or ""), "content": clip(copy.deepcopy(m.get("content")))}
            for m in messages if isinstance(m, dict)]


_WS = re.compile(r"\s+")

__all__ = ["CallBook", "NO_RECORD", "ReplayMemoryProvider", "ReplayResult", "ReplayTool", "build_plan", "build_registry",
           "replay_world"]
