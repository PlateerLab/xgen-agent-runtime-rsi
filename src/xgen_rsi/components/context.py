"""context_mgmt 구성요소 — 호출 전 메시지 구성: 기억 검색 주입, 비용 트리거 정리, 용량 트리거 압축, 예산 가드.

메커니즘(``prune_messages``, ``run_compaction`` + ``LLMSummaryCompactor``, ``estimate_prompt_tokens``,
``TokenBudgetGuard``)은 기존 런타임 함수를 쓰고, **언제 무엇을 할지**는 여기서 정한다. 임계값이 전부
파라미터라 RRSI 의 편집 대상이 된다(기존엔 0.8/0.7 이 코드에 박혀 있었다 — 조사 13 문서 §3.5).

압축 요약 호출은 ``state.llm_client``(원장 클라이언트)를 지나므로 원장에 ``compact`` 로 남는다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List

from xgen_rsi.harness.runtime import Component

logger = logging.getLogger(__name__)


class StandardContextComponent(Component):
    """Keeps the conversation inside the context window.

    First iteration: inject memory through the memory component's retriever (bounded by
    ``retrieval_timeout_s``). Cost-triggered pruning of old tool output when the estimated prompt
    exceeds ``prune_over_tokens``. Capacity-triggered summary compaction when the prompt exceeds
    window x ``proactive_ratio``, down to window x ``target_ratio``. Before each call the budget
    guard (``guard``) reserves headroom max(``guard_min_headroom``, max_tokens + ``guard_extra``),
    compacting once more or rejecting the call when it cannot fit.

    Params: prune_over_tokens (int, 30000), proactive_ratio (float, 0.8), target_ratio (float, 0.7),
    retrieval_timeout_s (float, 10.0), guard (bool, True), guard_min_headroom (int, 4096),
    guard_extra (int, 2048).
    """
    kind = "context_mgmt"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._compactor: Any = None

    # ── 손잡이 ─────────────────────────────────────────────────────────
    def _enabled(self, rt: Any) -> bool:
        return bool(rt.plan.pipeline_kwargs.get("enable_compaction", True))

    def _prune_over(self, rt: Any) -> int:
        from xgen_agent_runtime.core.context_prune import DEFAULT_PRUNE_OVER_TOKENS

        # 에이전트(노드)가 명시했으면 그 값(0 = 끔), 아니면 하네스 값, 그것도 없으면 런타임 기본.
        node = rt.plan.pipeline_kwargs.get("prune_over_tokens")
        if node is not None:
            return int(node or 0)
        value = self.param("prune_over_tokens", DEFAULT_PRUNE_OVER_TOKENS)
        return int(value or 0)

    def compactor(self) -> Any:
        if self._compactor is None:
            from xgen_agent_runtime.stages.s02_context.artifact.default.compactors import (
                LLMSummaryCompactor,
            )

            self._compactor = LLMSummaryCompactor()
        return self._compactor

    # ── 호출 전 ────────────────────────────────────────────────────────
    async def before_call(self, rt: Any) -> None:
        from xgen_agent_runtime.core.compaction import run_compaction
        from xgen_agent_runtime.core.context_prune import prune_messages
        from xgen_agent_runtime.core.shared_keys import SharedKeys
        from xgen_agent_runtime.core.token_estimate import estimate_prompt_tokens

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
            except Exception:  # noqa: BLE001 — 정리는 보조이지 관문이 아니다
                logger.debug("cost-triggered prune failed", exc_info=True)
            else:
                if any(metrics.get(k) for k in ("deduped", "images_stripped", "trimmed")):
                    state.shared.pop("_prompt_tokens_memo", None)
                    before = estimated
                    estimated = estimate_prompt_tokens(state)
                    rt.emit(
                        "context.pruned",
                        dict(metrics, trigger="cost", threshold_tokens=prune_over, tokens_before=before, tokens_after=estimated),
                    )

        budget = int(getattr(state, "context_window_budget", 0) or 0)
        proactive = float(self.param("proactive_ratio", 0.8))
        target = float(self.param("target_ratio", 0.7))
        if enabled and budget > 0:
            if forced:
                await run_compaction(state, self.compactor(), trigger="requested", provider=None, target_tokens=int(budget * target))
                state.shared.pop("_prompt_tokens_memo", None)
                estimated = estimate_prompt_tokens(state)
            elif estimated > budget * proactive:
                await run_compaction(state, self.compactor(), trigger="proactive", provider=None, target_tokens=int(budget * target))
                state.shared.pop("_prompt_tokens_memo", None)
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

    # ── 예산 가드 ──────────────────────────────────────────────────────
    async def ensure_fits(self, rt: Any) -> None:
        """다음 요청이 응답 헤드룸을 남기는지 — 모자라면 압축하고 1회 재검사, 그래도 안 되면 거절.

        기존 Stage 4 ``TokenBudgetGuard`` 와 같은 판정이다. 헤드룸 = max(4096, max_tokens + 2048),
        단 윈도우의 절반을 넘지 않는다(비정상 설정에서 매 턴 거절되지 않게).
        """
        if not self._enabled(rt) or not self.param("guard", True):
            return
        from xgen_agent_runtime.core.compaction import run_compaction
        from xgen_agent_runtime.core.errors import GuardRejectError
        from xgen_agent_runtime.stages.s04_guard.artifact.default.guards import TokenBudgetGuard

        state = rt.state
        max_tokens = int(rt.plan.pipeline_kwargs.get("max_tokens", 8192))
        window = int(getattr(state, "context_window_budget", 0) or 0)
        headroom = max(int(self.param("guard_min_headroom", 4096)), max_tokens + int(self.param("guard_extra", 2048)))
        if window > 0:
            headroom = min(headroom, max(1024, window // 2))
        guard = TokenBudgetGuard(min_remaining_tokens=headroom)
        result = guard.check(state)
        rt.emit("guard.check", {"passed": result.passed, "guard_name": result.guard_name, "message": result.message})
        if result.passed:
            return
        if result.action == "warn":
            rt.emit("guard.warn", {"message": result.message})
            return
        if result.action == "compact" and window > 0:
            await run_compaction(
                state,
                self.compactor(),
                trigger="guard",
                provider=None,
                target_tokens=int(window * float(self.param("target_ratio", 0.7))),
            )
            state.shared.pop("_prompt_tokens_memo", None)
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


def _last_user_text(messages: List[dict]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        return str(content)
    return ""
