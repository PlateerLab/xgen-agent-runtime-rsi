"""control_flow 구성요소 — 루프 결정의 **유일한 자리**.

기존 엔진은 같은 필드(``state.loop_decision``)에 다섯 곳이 차례로 썼다(조사 10 문서 §0-2). 여기서는 한
함수가 순서대로 정한다.

1. 도구가 돌았으면 계속(그 결과를 모델이 봐야 한다).
2. 완료 신호(선택 마커): ``complete`` → 완료, ``blocked`` → 사람 필요(escalate), ``error`` → 실패.
3. 도구 호출이 없는 응답 → 완료(구조 신호).
4. 완료 직전 산출물 대조(검토자가 문제를 찾으면 한 번 더 돈다 — 문제 없으면 왕복 0).
5. 반복 거부 종료, 턴 입력 예산(쌓이면 보고를 받고 끝낸다).

슬라이스 반복 한도(``max_iterations``)는 커널 한도다 — 여기서 늘릴 수 없다. 장치(검토자·반복 종료·
턴 예산)는 기존 런타임 구현을 쓰고, 켜기·끄기와 임계값이 파라미터다.
"""

from __future__ import annotations

from typing import Any, List, Optional

from xgen_rsi.harness.kinds import Decision, ParsedStep
from xgen_rsi.harness.runtime import Component


class StandardControlComponent(Component):
    """The single loop decision point after every model step.

    Order: tools ran -> continue; explicit signals (complete / blocked -> escalate / error); a reply
    without tool calls -> complete; before completing, the deliverable check (``completion_review``)
    compares the claimed output with the workspace; repeated identical failing calls end the turn after
    ``repeat_stop_after`` repeats; the per-turn input-token budget with soft and hard thresholds
    (``turn_budget_soft``, ``turn_budget_hard``). Kernel limits (max iterations, cost budget) are enforced by the
    kernel, not here, and cannot be changed by a harness.

    Params: completion_review (bool, True), repeat_stop_after (int, 3), turn_budget_soft (int),
    turn_budget_hard (int).
    """
    kind = "control_flow"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._reviewers: Optional[List[Any]] = None
        self._repeat_stop: Any = None
        self._turn_budget: Any = None
        self._wired = False

    def _wire(self, rt: Any) -> None:
        if self._wired:
            return
        self._wired = True
        kw = rt.plan.pipeline_kwargs
        self._reviewers = []
        if self.param("completion_review", True) and rt.plan.run_tool_context is not None and not rt.is_cli:
            from xgen_agent_runtime.stages.s16_loop.completion_review import DeliverableReviewer

            self._reviewers.append(DeliverableReviewer(rt.tool_context_provider))
        repeat_after = kw.get("repeat_stop_after", self.param("repeat_stop_after", 3))
        if repeat_after and int(repeat_after) > 0:
            from xgen_agent_runtime.stages.s16_loop.repeat_stop import RepeatStop

            self._repeat_stop = RepeatStop(stop_after=int(repeat_after))
        budget = kw.get("turn_input_budget_tokens", None)
        if budget is None and "turn_input_budget_tokens" not in kw:
            from xgen_agent_runtime.stages.s16_loop.turn_budget import (
                DEFAULT_HARD_TOKENS,
                DEFAULT_SOFT_TOKENS,
            )

            budget = (
                int(self.param("turn_budget_soft", DEFAULT_SOFT_TOKENS)),
                int(self.param("turn_budget_hard", DEFAULT_HARD_TOKENS)),
            )
        soft, hard = budget or (0, 0)
        if int(soft) > 0 and int(hard) > 0:
            from xgen_agent_runtime.stages.s16_loop.turn_budget import TurnInputBudget

            self._turn_budget = TurnInputBudget(soft_tokens=int(soft), hard_tokens=int(hard))

    async def decide(self, rt: Any, step: ParsedStep, tools_ran: bool) -> Decision:
        self._wire(rt)
        state = rt.state
        decision: Decision
        if tools_ran:
            decision = "continue"
        elif step.signal == "complete":
            decision = "complete"
        elif step.signal == "blocked":
            decision = "escalate"
        elif step.signal == "error":
            decision = "error"
        elif not step.tool_calls:
            decision = "complete"
        else:
            # 도구 호출이 있는데 실행되지 않았다(실행 경로 없음) — 더 돌아도 같은 자리다.
            decision = "complete"

        if decision == "complete" and self._reviewers:
            for reviewer in self._reviewers:
                note = await reviewer.review(state)
                if note:
                    state.add_message("user", note)
                    state.completion_signal = None
                    state.completion_detail = None
                    decision = "continue"
                    break

        if self._repeat_stop is not None:
            decision = self._repeat_stop.apply(state, decision)  # type: ignore[assignment]
        if self._turn_budget is not None:
            decision = self._turn_budget.apply(state, decision)  # type: ignore[assignment]

        rt.emit(
            f"loop.{decision}",
            {
                "iteration": state.iteration,
                "signal": state.completion_signal,
                "pending_tools": len(state.pending_tool_calls),
                "has_tool_results": bool(tools_ran),
            },
        )
        return decision
