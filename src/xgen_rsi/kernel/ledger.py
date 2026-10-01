"""사용량 원장 — 정책 π 에 대한 **모든** 호출의 단일 기록처. RRSI 의 c(τ) 는 여기서만 계산한다.

기존 엔진(geny)은 메인 루프 호출만 셌고(압축·증류·내부 도구 루프·재시도는 빠짐, 조사 12·13·14 문서), 그래서
RRSI 비용 규칙(Eq.7)의 ΔC 를 믿을 수 없었다. 여기서는 공급자 클라이언트를 :class:`LedgerClient` 로 감싸
턴의 모든 호출을 지나게 한다 — 커널 게이트웨이의 메인 호출이든, ``state.llm_client`` 를 쓰는 압축기든.

외부 계약(``usage`` 청크·``usage_sink``)은 기존 정의 그대로다(결정 D-7): 메인 루프 호출(+ 이 엔진이 새로
하는 탐색 시도)만 합산하고, 압축 같은 보조 호출은 내부 원장·기록에만 남긴다. 그래야 과금·쿼터 의미가
바뀌지 않는다.
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

#: 외부 usage 에 합산하는 purpose. 기존 엔진의 "메인 루프 호출"에 해당한다.
EXTERNAL_PURPOSES = frozenset({"main", "explore_attempt"})

_PURPOSE: contextvars.ContextVar[str] = contextvars.ContextVar("xgen_rsi_purpose", default="")


@contextlib.contextmanager
def purpose(name: str) -> Iterator[None]:
    """이 블록 안의 정책 호출에 purpose 를 붙인다(명시 ``purpose=`` 인자가 없는 호출에만)."""
    token = _PURPOSE.set(name)
    try:
        yield
    finally:
        _PURPOSE.reset(token)


def _canonical_purpose(explicit: str) -> str:
    """클라이언트 호출의 purpose 인자 → 원장 purpose.

    기존 코드가 넘기는 값: ``api``(s06 메인 호출), ``s02.compact``(요약 압축), ``memory.rollup``(증류),
    ``skill_fork:<id>``. 커널 게이트웨이는 ``main``/``explore_attempt``/``subagent``/``verify`` 를 넘긴다.
    """
    p = (explicit or "").strip()
    ctx = _PURPOSE.get()
    if not p:
        return ctx or "auxiliary"
    if p in ("api", "main"):
        return ctx if ctx in EXTERNAL_PURPOSES else "main"
    if p.startswith("s02.compact") or "compact" in p:
        return "compact"
    if p.startswith("memory."):
        return "distill"
    if p.startswith("skill_fork"):
        return "subagent"
    return p


@dataclass(frozen=True)
class CallRecord:
    """정책 호출 한 번."""

    seq: int
    purpose: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    reasoning: Optional[int]
    cli_num_turns: Optional[int]
    cost_usd_reported: Optional[float]
    duration_ms: int
    stop_reason: Optional[str]
    error: Optional[str] = None

    @property
    def policy_tokens(self) -> int:
        """c(τ) 의 이 호출 몫 — input + output (공급자 usage 의 input 정의를 그대로 따른다)."""
        return int(self.input_tokens) + int(self.output_tokens)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UsageLedger:
    """턴(τ) 하나의 원장."""

    provider: str = ""
    records: List[CallRecord] = field(default_factory=list)
    #: 외부 usage 를 만들 때 쓰는 호출별 TokenUsage(기존 ``state.turn_token_usage`` 와 같은 모양)
    external_usages: List[Any] = field(default_factory=list)
    _seq: int = 0

    def record(self, *, purpose_name: str, response: Any, provider: str, duration_ms: int, error: Optional[str] = None) -> CallRecord:
        usage = getattr(response, "usage", None)
        self._seq += 1
        rec = CallRecord(
            seq=self._seq,
            purpose=purpose_name,
            provider=provider,
            model=str(getattr(response, "model", "") or ""),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            reasoning=_reasoning_tokens(getattr(response, "raw", None)),
            cli_num_turns=_cli_num_turns(getattr(response, "raw", None)),
            cost_usd_reported=getattr(usage, "cost_usd", None),
            duration_ms=int(duration_ms),
            stop_reason=str(getattr(response, "stop_reason", "") or "") or None,
            error=error,
        )
        self.records.append(rec)
        if purpose_name in EXTERNAL_PURPOSES and usage is not None:
            self.external_usages.append(usage)
        return rec

    def record_failure(self, *, purpose_name: str, provider: str, model: str, duration_ms: int, error: str) -> None:
        self._seq += 1
        self.records.append(
            CallRecord(
                seq=self._seq,
                purpose=purpose_name,
                provider=provider,
                model=model,
                input_tokens=0,
                output_tokens=0,
                cache_read=0,
                cache_write=0,
                reasoning=None,
                cli_num_turns=None,
                cost_usd_reported=None,
                duration_ms=int(duration_ms),
                stop_reason=None,
                error=error[:300],
            )
        )

    # ── RRSI 측정 ─────────────────────────────────────────────────────
    def policy_tokens(self) -> int:
        """c(τ) = 이 턴의 모든 정책 호출의 (input + output) 합 — purpose 무관(설계 05 §2.2)."""
        return sum(r.policy_tokens for r in self.records)

    def by_purpose(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {}
        for r in self.records:
            slot = out.setdefault(r.purpose, {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
            slot["calls"] += 1
            slot["input"] += r.input_tokens
            slot["output"] += r.output_tokens
            slot["cache_read"] += r.cache_read
            slot["cache_write"] += r.cache_write
        return out

    def model_calls(self, purposes: Optional[frozenset] = None) -> int:
        return sum(1 for r in self.records if purposes is None or r.purpose in purposes)

    # ── 외부 계약 ─────────────────────────────────────────────────────
    def external_usage_payload(
        self,
        *,
        model: str,
        provider: str,
        turn_cost_usd: float,
        harness: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """기존 ``runner.turn_usage`` 와 같은 모양·같은 의미의 usage 페이로드. 외부 호출이 0회면 None."""
        from xgen_rsi.base.core.state import TokenUsage

        calls = [u for u in self.external_usages if isinstance(u, TokenUsage)]
        if not calls:
            return None
        total = TokenUsage()
        for u in calls:
            total += u
        cost: Optional[float] = total.cost_usd
        if cost is None:
            cost = float(turn_cost_usd) if turn_cost_usd and turn_cost_usd > 0 else None
        # Anthropic/Bedrock 은 캐시분을 input_tokens 밖에서 따로 보고하고, OpenAI 계열은 안에 포함한다 —
        # 더하면 이중 집계다(기존 turn_usage 와 같은 규칙).
        cache_separate = provider in ("anthropic", "bedrock") or (
            not provider and str(model).startswith("claude")
        )
        per_call_prompt = [
            int(u.input_tokens)
            + (int(u.cache_read_input_tokens) + int(u.cache_creation_input_tokens) if cache_separate else 0)
            for u in calls
        ]
        usage: Dict[str, Any] = {
            "input_tokens": int(total.input_tokens),
            "output_tokens": int(total.output_tokens),
            "cache_read_tokens": int(total.cache_read_input_tokens),
            "cache_creation_tokens": int(total.cache_creation_input_tokens),
            "total_cost_usd": float(cost) if cost is not None else None,
            "model": model or None,
            "provider": provider or None,
            "calls": len(per_call_prompt),
            "first_call_prompt_tokens": per_call_prompt[0] if per_call_prompt else 0,
            "max_call_prompt_tokens": max(per_call_prompt) if per_call_prompt else 0,
        }
        if harness:
            usage["harness"] = harness
        return usage


class LedgerClient:
    """공급자 클라이언트를 감싸 모든 호출을 원장에 남긴다. 나머지 속성은 그대로 위임한다.

    ``state.llm_client`` 자리에 이것을 두면 압축기처럼 클라이언트를 직접 부르는 기존 메커니즘의 호출도
    원장에 들어온다. 커널 게이트웨이도 같은 객체를 쓴다.
    """

    def __init__(self, inner: Any, ledger: UsageLedger) -> None:
        self._inner = inner
        self._ledger = ledger
        self.provider = getattr(inner, "provider", "")

    @property
    def inner(self) -> Any:
        return self._inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def create_message(self, **kwargs: Any) -> Any:
        purpose_name = _canonical_purpose(str(kwargs.get("purpose") or ""))
        model = str(getattr(kwargs.get("model_config"), "model", "") or "")
        started = time.monotonic()
        try:
            response = await self._inner.create_message(**kwargs)
        except BaseException as exc:
            self._ledger.record_failure(
                purpose_name=purpose_name,
                provider=str(self.provider or ""),
                model=model,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._ledger.record(
            purpose_name=purpose_name,
            response=response,
            provider=str(self.provider or ""),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return response

    def create_message_stream(self, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        purpose_name = _canonical_purpose(str(kwargs.get("purpose") or ""))
        model = str(getattr(kwargs.get("model_config"), "model", "") or "")
        inner_stream = self._inner.create_message_stream(**kwargs)
        return _LedgeredStream(inner_stream, self._ledger, purpose_name, str(self.provider or ""), model)


class _LedgeredStream:
    """스트림을 그대로 흘리면서 끝 프레임(``message_complete``)의 usage 를 원장에 남긴다."""

    def __init__(self, inner: Any, ledger: UsageLedger, purpose_name: str, provider: str, model: str) -> None:
        self._inner = inner
        self._iter: Any = None
        self._ledger = ledger
        self._purpose = purpose_name
        self._provider = provider
        self._model = model
        self._started = time.monotonic()
        self._recorded = False

    def __aiter__(self) -> "_LedgeredStream":
        self._iter = self._inner.__aiter__()
        return self

    async def __anext__(self) -> Dict[str, Any]:
        try:
            chunk = await self._iter.__anext__()
        except StopAsyncIteration:
            raise
        except BaseException as exc:
            if not self._recorded:
                self._recorded = True
                self._ledger.record_failure(
                    purpose_name=self._purpose,
                    provider=self._provider,
                    model=self._model,
                    duration_ms=int((time.monotonic() - self._started) * 1000),
                    error=f"{type(exc).__name__}: {exc}",
                )
            raise
        if isinstance(chunk, dict) and chunk.get("type") == "message_complete" and not self._recorded:
            self._recorded = True
            self._ledger.record(
                purpose_name=self._purpose,
                response=chunk.get("response"),
                provider=self._provider,
                duration_ms=int((time.monotonic() - self._started) * 1000),
            )
        return chunk

    async def aclose(self) -> None:
        target = self._iter if self._iter is not None else self._inner
        aclose = getattr(target, "aclose", None)
        if aclose is not None:
            await aclose()


def _reasoning_tokens(raw: Any) -> Optional[int]:
    """공급자 원응답에서 reasoning 토큰을 찾는다(없으면 None). TokenUsage 에는 이 칸이 없다."""
    usage = _get(raw, "usage") or _get(raw, "usage_metadata")
    if usage is None:
        return None
    details = _get(usage, "completion_tokens_details") or _get(usage, "output_tokens_details")
    for candidate in (
        _get(details, "reasoning_tokens"),
        _get(usage, "thoughts_token_count"),
        _get(usage, "reasoning_tokens"),
    ):
        if isinstance(candidate, (int, float)) and candidate >= 0:
            return int(candidate)
    return None


def _cli_num_turns(raw: Any) -> Optional[int]:
    value = _get(raw, "num_turns")
    return int(value) if isinstance(value, (int, float)) else None


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
