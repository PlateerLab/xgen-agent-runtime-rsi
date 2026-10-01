"""output_plumbing 구성요소 — 모델 응답에서 글·도구 호출·완료 신호를 읽고, 구조화 출력을 정착시킨다.

파서·신호 탐지기는 바탕 런타임(``xgen_rsi.base``)의 것을 쓴다(``DefaultParser``/``StructuredOutputParser``,
``RegexDetector``). 완료 신호 마커(``[COMPLETE]`` 등)는 **선택 신호**일 뿐이다 — 이 하네스의 기본 완료 판정은
"도구 호출이 없는 응답"이라는 구조 신호다(control_flow 쪽, 설계 30 문서 §6).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from xgen_rsi.harness.kinds import ParsedStep
from xgen_rsi.harness.runtime import Component


class ParseOutputComponent(Component):
    """Parses each model response into text, tool calls and signals; settles structured output.

    With an output schema the structured parser is used and the final answer is settled against the
    schema at the end of the turn. ``signal_detector`` picks how completion/blocked/error signals are
    read from text ("regex", "structured" or "hybrid"); ``completion_markers`` turns signal detection
    on or off.

    Params: signal_detector (str, "regex"), completion_markers (bool, True).
    """
    kind = "output_plumbing"

    def __init__(self, spec: Any) -> None:
        super().__init__(spec)
        self._parser: Any = None
        self._detector: Any = None

    def _ensure(self, schema: Optional[Dict[str, Any]]) -> None:
        if self._parser is not None:
            return
        from xgen_rsi.base.stages.s09_parse.artifact.default.parsers import (
            DefaultParser,
            StructuredOutputParser,
        )
        from xgen_rsi.base.stages.s09_parse.artifact.default.signals import (
            HybridDetector,
            RegexDetector,
            StructuredDetector,
        )

        self._parser = StructuredOutputParser(schema=schema) if schema else DefaultParser()
        detector = str(self.param("signal_detector", "regex"))
        self._detector = {"structured": StructuredDetector, "hybrid": HybridDetector}.get(
            detector, RegexDetector
        )()

    def parse(self, rt: Any, response: Any) -> ParsedStep:
        self._ensure(getattr(rt.plan, "schema", None))
        parsed = self._parser.parse(response)
        step = ParsedStep(
            text=parsed.text,
            tool_calls=[
                {"tool_use_id": tc.tool_use_id, "tool_name": tc.tool_name, "tool_input": tc.tool_input}
                for tc in parsed.tool_calls
            ],
            stop_reason=getattr(parsed, "stop_reason", None),
            thinking_texts=list(getattr(parsed, "thinking_texts", None) or []),
        )
        if parsed.text and self.param("completion_markers", True):
            from xgen_rsi.base.stages.s09_parse.interface import CompletionSignal

            signal, detail = self._detector.detect(parsed.text)
            if signal != CompletionSignal.NONE:
                step.signal = signal.value
                step.signal_detail = detail
        state = rt.state
        state.completion_signal = step.signal
        state.completion_detail = step.signal_detail
        state.pending_tool_calls = list(step.tool_calls)
        for txt in step.thinking_texts:
            state.thinking_history.append({"iteration": state.iteration, "text": txt})
        state.final_text = step.text
        rt.emit(
            "parse.complete",
            {
                "text_length": len(step.text),
                "tool_calls": len(step.tool_calls),
                "signal": step.signal,
                "stop_reason": step.stop_reason,
            },
        )
        return step

    def settle(self, text: str, schema: Optional[Dict[str, Any]]) -> str:
        if not schema:
            return text
        from xgen_rsi.base.host.runner import settle_structured

        return settle_structured(text, schema)
