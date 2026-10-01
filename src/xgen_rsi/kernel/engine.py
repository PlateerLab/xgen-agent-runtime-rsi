"""턴 엔진 — 슬라이스 하나를 돈다. 21칸 번호 대신 **구성요소 종류**로 짠 실행 코어.

한 반복의 순서::

    context.before_call   기억 주입(첫 반복) · 비용 트리거 정리 · 용량 트리거 압축
    prompt.build          시스템 프롬프트(안정 접두) + 이번 턴 맥락(휘발)
    client_tool           이번 호출의 도구 표면
    context.ensure_fits   예산 가드(압축 후 1회 재검사)
    config.apply_request  요청 모양 노브(프롬프트 캐시 표시 등)
    gateway.call          정책 호출(원장 기록, 스트림 사건)
    output.parse          글·도구 호출·완료 신호
    tools.run             도구 실행(커널 — 권한·거부·반복 차단)
    control.decide        계속/완료/중단 — 유일한 결정 자리

커널 한도(슬라이스 반복 수·턴 비용 상한)는 결정 뒤에 확인한다 — 하네스는 한도를 늘릴 수 없다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from xgen_rsi.base.core.run_status import RunStatus, TerminationReason
from xgen_rsi.harness.runtime import TurnRuntime
from xgen_rsi.kernel.model_call import ModelCaller
from xgen_rsi.kernel.recorder import TrajectoryRecorder
from xgen_rsi.kernel.tools import ToolRunner

logger = logging.getLogger(__name__)


@dataclass
class SliceOutcome:
    status: str
    resumable: bool
    termination_reason: str
    final_text: str
    checkpoint_id: Optional[str] = None


class TurnEngine:
    def __init__(
        self,
        rt: TurnRuntime,
        caller: ModelCaller,
        tools: Optional[ToolRunner],
        recorder: Optional[TrajectoryRecorder] = None,
    ) -> None:
        self.rt = rt
        self.caller = caller
        self.tools = tools
        self.recorder = recorder
        h = rt.harness
        self.prompt = h.one("prompt")
        self.context = h.one("context_mgmt")
        self.control = h.one("control_flow")
        self.output = h.one("output_plumbing")
        self.tool_policy = h.maybe("client_tool")
        self.memory = h.maybe("memory")
        self.configs = h.all("config")
        self._pricing: Any = None

    # ── 입력 ────────────────────────────────────────────────────────────
    def _accept_input(self, pipeline_input: Any, continuation: bool) -> None:
        from xgen_rsi.base.core.errors import StageError
        from xgen_rsi.base.core.message_repair import repair_dangling_tool_calls
        from xgen_rsi.base.stages.s01_input.artifact.default.normalizers import (
            DefaultNormalizer,
        )
        from xgen_rsi.base.stages.s01_input.artifact.default.validators import DefaultValidator

        state = self.rt.state
        repaired = repair_dangling_tool_calls(state.messages)
        if repaired:
            state.add_event("input.tool_calls_repaired", {"count": repaired})
        if continuation:
            state.add_event("input.continuation", {"message_count": len(state.messages)})
            return
        error = DefaultValidator().validate(pipeline_input)
        if error:
            raise StageError(f"Input validation failed: {error}", stage_name="input", stage_order=1)
        normalized = DefaultNormalizer().normalize(pipeline_input)
        normalized.session_id = state.session_id
        state.add_message("user", normalized.to_message_content())
        state.add_event("input.normalized", {"text_length": len(normalized.text)})

    # ── 사용량(기존 Stage 7 과 같은 기록 — 턴 예산·외부 usage 가 읽는다) ─────
    def _track_usage(self, response: Any) -> None:
        state = self.rt.state
        usage = response.usage
        state.token_usage += usage
        state.turn_token_usage.append(usage)
        if self._pricing is None:
            from xgen_rsi.base.stages.s07_token.artifact.default.pricing import (
                AnthropicPricingCalculator,
            )

            self._pricing = AnthropicPricingCalculator()
        try:
            cost = float(self._pricing.calculate(usage, state.model))
        except Exception:  # noqa: BLE001 — 가격 계산 실패는 0(기존과 같이 미상 처리)
            cost = 0.0
        state.accumulate_cost(cost)
        if usage.cache_creation_input_tokens > 0:
            state.cache_metrics.total_cache_writes += 1
        if usage.cache_read_input_tokens > 0:
            state.cache_metrics.total_cache_reads += 1
        state.add_event(
            "token.tracked",
            {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_write": usage.cache_creation_input_tokens,
                "cache_read": usage.cache_read_input_tokens,
                "cost_usd": cost,
                "total_cost_usd": state.total_cost_usd,
            },
        )

    # ── 한 슬라이스 ─────────────────────────────────────────────────────
    async def run_slice(self, pipeline_input: Any, *, continuation: bool) -> SliceOutcome:
        from xgen_rsi.base.stages.s06_api.artifact.default.tool_loop import (
            assistant_content_blocks,
        )

        rt = self.rt
        state = rt.state
        if continuation:
            state.begin_continuation_slice()
        if self.recorder is not None:
            self.recorder.slice_started()
        self._accept_input(pipeline_input, continuation)

        while True:
            await self.context.before_call(rt)
            built = self.prompt.build(rt)
            state.system = built.system
            if built.turn_context:
                state.shared["turn_context_text"] = built.turn_context
            else:
                state.shared.pop("turn_context_text", None)
            state.tools = self.tool_policy.exposed_tools(rt) if (self.tool_policy is not None and self.tools is not None) else []
            rt.emit(
                "system.built",
                {
                    "prompt_type": "content_blocks" if isinstance(state.system, list) else "string",
                    "prompt_length": (
                        sum(len(b.get("text", "")) for b in state.system)
                        if isinstance(state.system, list)
                        else len(str(state.system or ""))
                    ),
                    "tools_count": len(state.tools),
                    "turn_context_chars": len(built.turn_context),
                    "harness": rt.harness.version_id,
                },
            )
            await self.context.ensure_fits(rt)
            for cfg in self.configs:
                cfg.apply_request(rt)

            calls_before = rt.ledger.model_calls()
            tokens_before = rt.ledger.policy_tokens()
            response = await self.caller.call(state, rt.model_config, purpose="main")
            state.last_api_response = response
            state.add_message("assistant", assistant_content_blocks(response))
            self._track_usage(response)

            step = self.output.parse(rt, response)
            tools_ran = False
            tool_errors = 0
            if step.tool_calls and self.tools is not None:
                results = await self.tools.run(state, step.tool_calls)
                tools_ran = True
                tool_errors = sum(1 for r in results if r.get("is_error"))
            decision = await self.control.decide(rt, step, tools_ran)
            state.loop_decision = decision
            state.tool_results = []
            if self.recorder is not None:
                self.recorder.step(
                    attempt=state.iteration,
                    tool_calls=len(step.tool_calls),
                    tool_errors=tool_errors,
                    decision=decision,
                    policy_tokens=rt.ledger.policy_tokens() - tokens_before,
                    model_calls=rt.ledger.model_calls() - calls_before,
                )
            if decision != "continue":
                break

            state.iteration += 1
            if state.is_over_iterations:
                state.loop_decision = "suspend"
                state.completion_signal = "MAX_ITERATIONS"
                state.mark_suspended(
                    TerminationReason.MAX_ITERATIONS_PER_SLICE.value,
                    detail=f"Execution slice reached max_iterations={state.max_iterations}; continuation state is preserved.",
                )
                state.add_event(
                    "loop.suspended",
                    {
                        "reason": TerminationReason.MAX_ITERATIONS_PER_SLICE.value,
                        "iteration": state.iteration,
                        "max_iterations": state.max_iterations,
                        "resumable": True,
                    },
                )
                break
            if state.is_over_budget:
                state.loop_decision = "escalate"
                state.completion_signal = "COST_BUDGET"
                state.mark_blocked(TerminationReason.COST_BUDGET.value, detail="Cost budget exhausted; continuation requires a new budget.")
                state.add_event(
                    "loop.blocked",
                    {"reason": TerminationReason.COST_BUDGET.value, "total_cost_usd": state.total_cost_usd, "budget_usd": state.cost_budget_usd},
                )
                break

        if state.run_status == RunStatus.RUNNING.value:
            if state.loop_decision == "complete":
                state.mark_completed()
            elif state.loop_decision == "suspend":
                state.mark_suspended(TerminationReason.MAX_ITERATIONS_PER_SLICE.value)
            elif state.loop_decision == "escalate":
                state.mark_blocked(TerminationReason.USER_INPUT_REQUIRED.value, detail=state.completion_detail)
            elif state.loop_decision == "error":
                state.mark_failed(state.completion_detail or "Pipeline loop failed")

        if self.memory is not None:
            await self.memory.on_slice_end(rt)
        state.add_event(
            "yield.complete",
            {"text_length": len(state.final_text or ""), "iterations": state.iteration, "total_cost_usd": state.total_cost_usd},
        )
        return SliceOutcome(
            status=str(state.run_status),
            resumable=bool(state.resumable),
            termination_reason=str(state.termination_reason or ""),
            final_text=str(state.final_text or ""),
            checkpoint_id=state.checkpoint_id,
        )
