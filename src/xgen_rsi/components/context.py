"""context_mgmt 구성요소. 호출 전 메시지 구성: 기억 검색 주입, 비용 트리거 정리, 용량 트리거 압축, 예산 가드, 넘침 복구.

메커니즘(``prune_messages``, ``run_compaction`` + ``LLMSummaryCompactor``, ``estimate_prompt_tokens``, ``soft_level``,
``TokenBudgetGuard``)은 바탕 런타임(``xgen_rsi.base``)의 것을 쓰고, **언제 무엇을 할지**는 런타임 4.89.0 의 압축 정책
(``core.compaction``)을 따른다. 문턱은 입력 예산(창에서 답 몫을 뺀 것) 가운데 압축이 줄일 수 없는 몫(시스템 프롬프트·
도구·턴 맥락·지금 요청의 첨부)을 뺀 나머지의 비율이다(``soft_level``).

RRSI 가 고칠 수 있는 것은 약한 압박의 두 비율(``proactive_ratio``, ``target_ratio``)과 비용 트리거·검색 시간 상한뿐이다.
비율은 범위 안으로 묶는다(``proactive_ratio`` 0.5~0.9, ``target_ratio`` 0.2~``proactive_ratio`` - 0.1). 예산 가드와 넘침
복구는 끌 수도 고칠 수도 없다: 가드의 헤드룸은 정책의 답 몫(``TokenBudgetGuard()`` 기본값)이고, 넘침 복구는 런타임의
``TARGET_RATIO`` 를 쓴다.

압축 요약 호출은 ``state.llm_client``(원장 클라이언트)를 지나므로 원장에 ``compact`` 로 남는다.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, List, Tuple

from xgen_rsi.harness.runtime import Component

logger = logging.getLogger(__name__)

#: 약한 압박 문턱의 허용 범위. 0.5 아래는 거의 매 호출 요약하고, 0.9 위는 가드보다 늦다.
PROACTIVE_RANGE = (0.5, 0.9)
#: 압축 목표의 하한과, 문턱 아래로 둘 최소 간격. 목표가 문턱에 붙으면 몇 호출마다 다시 압축한다.
TARGET_FLOOR = 0.2
TARGET_GAP = 0.1


def _ratio_param(value: Any, default: float) -> float:
    """하네스 파라미터 → 비율. 숫자가 아니거나(불리언 포함) NaN 이면 기본값."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return default if math.isnan(number) else number


class StandardContextComponent(Component):
    """Keeps the conversation inside the context window (runtime 4.89 compaction policy).

    Editable: ``prune_over_tokens`` (prune old tool output when the estimated prompt is larger),
    ``proactive_ratio`` (relief starts above this share of the compressible input budget, clamped to
    0.5 to 0.9), ``target_ratio`` (relief aims at this share, clamped to 0.2 up to proactive_ratio
    minus 0.1), ``retrieval_timeout_s`` (first-iteration memory injection). A value that is not a
    number falls back to the default. Not editable: the budget guard (keeps the answer's room free,
    compacts hard and re-checks once, else rejects the call) and the recovery after a provider
    rejects a request as too long (compacts hard and sends it once more).

    The input budget is the window minus the room kept for the answer; the compressible share leaves
    out the system prompt, tools, turn context and the current request's attachments. Relief prunes
    deterministically first and asks the model for a summary only when that is not enough.

    Params: prune_over_tokens (int, 30000), proactive_ratio (float, 0.8), target_ratio (float, 0.5),
    retrieval_timeout_s (float, 10.0).
    """
    kind = "context_mgmt"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._compactor: Any = None

    # ── 손잡이 ─────────────────────────────────────────────────────────
    def _enabled(self, rt: Any) -> bool:
        return bool(rt.plan.pipeline_kwargs.get("enable_compaction", True))

    def _prune_over(self, rt: Any) -> int:
        from xgen_rsi.base.core.context_prune import DEFAULT_PRUNE_OVER_TOKENS

        # 에이전트(노드)가 명시했으면 그 값(0 = 끔), 아니면 하네스 값, 그것도 없으면 런타임 기본.
        node = rt.plan.pipeline_kwargs.get("prune_over_tokens")
        if node is not None:
            return int(node or 0)
        value = self.param("prune_over_tokens", DEFAULT_PRUNE_OVER_TOKENS)
        return int(value or 0)

    def ratios(self) -> Tuple[float, float]:
        """(약한 압박 문턱, 압축 목표): 하네스 값을 허용 범위로 묶은 것. H0 는 런타임 값(0.8, 0.5) 그대로다."""
        from xgen_rsi.base.core.compaction import PROACTIVE_RATIO, TARGET_RATIO

        low, high = PROACTIVE_RANGE
        proactive = min(high, max(low, _ratio_param(self.param("proactive_ratio", PROACTIVE_RATIO), PROACTIVE_RATIO)))
        target = _ratio_param(self.param("target_ratio", TARGET_RATIO), TARGET_RATIO)
        target = round(min(proactive - TARGET_GAP, max(TARGET_FLOOR, target)), 6)
        return proactive, target

    def compactor(self) -> Any:
        if self._compactor is None:
            from xgen_rsi.base.stages.s02_context.artifact.default.compactors import (
                LLMSummaryCompactor,
            )

            self._compactor = LLMSummaryCompactor()
        return self._compactor

    # ── 호출 전 (기존 Stage 2) ──────────────────────────────────────────
    async def before_call(self, rt: Any) -> None:
        from xgen_rsi.base.core.compaction import run_compaction, soft_level
        from xgen_rsi.base.core.context_prune import prune_messages
        from xgen_rsi.base.core.shared_keys import SharedKeys
        from xgen_rsi.base.core.token_estimate import estimate_prompt_tokens

        state = rt.state
        first = rt.iteration == 0 and not rt.is_continuation
        if first:
            state.metadata.pop("memory_context", None)
            state.metadata.pop("memory_pinned", None)
            await self._inject_memory(rt)

        estimated = estimate_prompt_tokens(state)
        forced = state.shared.pop(SharedKeys.CONTEXT_COMPACTION_REQUEST, None)
        enabled = self._enabled(rt)

        prune_over = self._prune_over(rt)
        if enabled and prune_over and estimated > prune_over:
            try:
                metrics = prune_messages(state.messages or [])
            except Exception:  # noqa: BLE001 (정리는 보조이지 관문이 아니다)
                logger.debug("cost-triggered prune failed", exc_info=True)
            else:
                if any(metrics.get(k) for k in ("deduped", "images_stripped", "trimmed", "inputs_trimmed")):
                    state.shared.pop("_prompt_tokens_memo", None)
                    before = estimated
                    estimated = estimate_prompt_tokens(state)
                    rt.emit(
                        "context.pruned",
                        dict(metrics, trigger="cost", threshold_tokens=prune_over, tokens_before=before, tokens_after=estimated),
                    )

        if enabled:
            proactive, target = self.ratios()
            if forced:
                # Stage 16 의 압축 요청이나 /compact: 문턱과 무관하게 지금 줄인다(사람이 건 것만 "manual").
                manual = isinstance(forced, dict) and forced.get("source") == "manual"
                await run_compaction(
                    state,
                    self.compactor(),
                    trigger="manual" if manual else "requested",
                    provider=None,
                    target_tokens=soft_level(state, target),
                )
                state.shared.pop("_prompt_tokens_memo", None)
                estimated = estimate_prompt_tokens(state)
            elif estimated > soft_level(state, proactive):
                await run_compaction(
                    state,
                    self.compactor(),
                    trigger="proactive",
                    provider=None,
                    target_tokens=soft_level(state, target),
                )
                estimated = estimate_prompt_tokens(state)
        elif forced:
            rt.emit("context.compaction_failed", {"trigger": "requested", "error": "runtime compaction is disabled for this provider"})

        rt.emit(
            "context.built",
            {"message_count": len(state.messages), "memory_refs": len(state.memory_refs), "estimated_tokens": estimated},
        )

    async def _inject_memory(self, rt: Any) -> None:
        memory = rt.harness.maybe("memory")
        retriever = memory.retriever(rt) if memory is not None else None
        if retriever is None:
            return
        state = rt.state
        query = _last_user_text(state.messages)
        timeout = float(self.param("retrieval_timeout_s", 10.0)) or None
        try:
            chunks: List[Any] = list(await asyncio.wait_for(retriever.retrieve(query, state), timeout=timeout))
        except asyncio.TimeoutError:
            rt.emit("context.retrieval_timeout", {"timeout_s": timeout})
            return
        capture = getattr(rt, "capture", None)
        if capture is not None:
            try:
                capture.on_memory(chunks)
            except Exception:  # noqa: BLE001 — 세계 기록이 턴을 깨지 않는다
                logger.debug("world capture of memory failed", exc_info=True)
        if not chunks:
            return
        seen = {ref.get("key") for ref in state.memory_refs}
        for chunk in chunks:
            if chunk.key not in seen:
                state.memory_refs.append(
                    {
                        "key": chunk.key,
                        "source": chunk.source,
                        "content_length": len(chunk.content),
                        "relevance": chunk.relevance_score,
                    }
                )
                seen.add(chunk.key)
        pinned = [c for c in chunks if c.source == "pinned" or (c.metadata or {}).get("layer") == "pinned"]
        others = [c for c in chunks if c not in pinned]
        if state.messages:
            if pinned:
                state.metadata["memory_pinned"] = "\n\n".join(c.content for c in pinned)
            if others:
                state.metadata["memory_context"] = "\n".join(f"- [{c.source}] {c.key}: {c.content}" for c in others)

    # ── 예산 가드 (기존 Stage 4) ────────────────────────────────────────
    async def ensure_fits(self, rt: Any) -> None:
        """다음 요청이 답 몫을 남기는지. 모자라면 강하게 압축하고 1회 재검사, 그래도 안 되면 거절한다.

        먼저 Stage 2 와 같은 약한 압박 점검을 한 번 더 한다. 첫 반복에서 추정에 시스템 프롬프트와 도구가 다 든
        첫 자리가 여기다. 가드는 ``TokenBudgetGuard()`` 기본값(헤드룸 0 = 점검마다 상태에서 계산하는 정책의 답 몫)이라
        약한 압박이 늘 가드보다 먼저 온다(런타임 4.89.0 의 runner 가 짓는 가드와 같다).
        """
        if not self._enabled(rt):
            return
        from xgen_rsi.base.core.compaction import run_compaction, soft_level
        from xgen_rsi.base.core.errors import GuardRejectError
        from xgen_rsi.base.core.token_estimate import estimate_prompt_tokens
        from xgen_rsi.base.stages.s04_guard.artifact.default.guards import TokenBudgetGuard

        state = rt.state
        proactive, target = self.ratios()
        if estimate_prompt_tokens(state) > soft_level(state, proactive):
            await run_compaction(
                state,
                self.compactor(),
                trigger="proactive",
                provider=None,
                target_tokens=soft_level(state, target),
            )
        guard = TokenBudgetGuard()
        result = guard.check(state)
        rt.emit("guard.check", {"passed": result.passed, "guard_name": result.guard_name, "message": result.message})
        if result.passed:
            return
        if result.action == "warn":
            rt.emit("guard.warn", {"message": result.message})
            return
        if result.action == "compact":
            rt.emit("guard.compacting", {"guard_name": result.guard_name, "reason": result.message})
            outcome = await run_compaction(
                state,
                self.compactor(),
                trigger="guard",
                provider=None,
                # 이미 여기에 닿는 정리면 충분하고, 아니면 여기까지 요약한다.
                target_tokens=soft_level(state, target),
                # 요청이 이대로는 못 나간다: 공격적 정리, 작은 구간 건너뛰기 없음, 요약이 실패하면 결정적 다이제스트.
                hard=True,
            )
            if outcome.get("ok"):
                result = guard.check(state)
                rt.emit(
                    "guard.check",
                    {"passed": result.passed, "guard_name": result.guard_name, "message": result.message, "recheck": True},
                )
                if result.passed:
                    return
                if result.action == "warn":
                    rt.emit("guard.warn", {"message": result.message})
                    return
        raise GuardRejectError(result.message, guard_name=result.guard_name)

    # ── 넘침 복구 (기존 Stage 6 의 ``_recover_overflow``) ──────────────────
    async def recover_overflow(self, rt: Any, error: Any) -> bool:
        """공급자가 요청이 너무 길다고 거절한 뒤 이력을 강하게 줄인다. 다시 보낼 만하면 True.

        커널이 한 요청에 한 번만 부른다(다시 보낸 요청이 또 거절되면 그대로 올라간다). 압축이 꺼진 턴은 False.
        """
        if not self._enabled(rt):
            return False
        from xgen_rsi.base.core.compaction import (
            TARGET_RATIO,
            run_compaction,
            soft_level,
            window_of,
        )
        from xgen_rsi.base.core.errors import parse_overflow_limit, parse_overflow_tokens
        from xgen_rsi.base.core.token_estimate import (
            calibrate_from_overflow,
            estimate_prompt_tokens,
        )

        state = rt.state
        reported = parse_overflow_tokens(str(error))
        limit = parse_overflow_limit(str(error))
        estimated = estimate_prompt_tokens(state)
        believed = window_of(state)
        # 목표는 방금 틀린 추정이 아니라 공급자가 센 수에 맞춘다.
        calibrate_from_overflow(state, reported)
        if 0 < limit < believed:
            # 창 표나 카탈로그가 이 배포에 맞지 않았다. 이 턴의 남은 동안은 공급자의 수가 이긴다.
            state.context_window_budget = limit
        rt.emit(
            "context.overflow",
            {
                "message": str(error)[:500],
                "reported_tokens": reported,
                "estimated_tokens": estimated,
                "window": believed,
                "provider_limit": limit,
            },
        )
        if 0 < reported <= soft_level(state, TARGET_RATIO):
            # 메시지는 이미 작고 답 몫 때문에 넘쳤다(vLLM 은 max_tokens 를 센다). 압축으로 못 고친다.
            return False
        outcome = await run_compaction(
            state,
            self.compactor(),
            trigger="overflow",
            provider=None,
            # 목표 없음: 공급자가 이미 안 들어간다고 했으니 정리로 될 것 같아도 압축기를 돌린다.
            target_tokens=None,
            hard=True,
        )
        return bool(outcome.get("ok")) and (
            bool(outcome.get("summarized")) or int(outcome.get("saved_tokens_estimate") or 0) > 0
        )


def _last_user_text(messages: List[dict]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        return str(content)
    return ""
