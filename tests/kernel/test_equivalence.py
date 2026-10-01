"""두 엔진(기존 21-stage, RSI)이 같은 입력에 같은 외부 출력을 내는지 — I/O 계약 동등성(설계 32 문서 §5).

같은 각본 클라이언트·같은 호스트로 기존 엔진 ``AgentTurnExecutor().run``(geny)과 ``GenyRSITurnExecutor().run``(geny-rsi)을
돌려 비교한다 — 두 진입점은 같은 계약이고, 기존 런타임은 geny-rsi 를 모른다.

* 청크 열(시각·소요 시간만 정규화) — G-1/G-2/G-3
* usage 페이로드 — U-1
* 모델이 받은 요청(시스템 프롬프트·메시지·도구) — 하네스 H0 가 기존 동작과 같은 프롬프트를 내는지
* 이어가기·중단 안내 — X-1 일부
"""

from __future__ import annotations

import re
from typing import Any, Dict

import pytest
from xgen_agent_runtime.host import runner as runner_mod
from xgen_agent_runtime.host.turn_executor import AgentTurnExecutor

from tests.kernel.fakes import EchoTool, FakeHost, ScriptedClient, error_step, text_step, tool_step
from xgen_rsi.turn_executor import GenyRSITurnExecutor

_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2})?")
_DATE_LINE = re.compile(r"(Current date|현재|Today)[^\n]*", re.I)


def _norm(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items() if k not in ("duration_ms",)}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, str):
        return _TS.sub("<TS>", value)
    return value


def _norm_request(req: Dict[str, Any]) -> Dict[str, Any]:
    def strip_dates(x: Any) -> Any:
        if isinstance(x, str):
            return _DATE_LINE.sub("<DATE>", _TS.sub("<TS>", x))
        if isinstance(x, list):
            return [strip_dates(v) for v in x]
        if isinstance(x, dict):
            return {k: strip_dates(v) for k, v in x.items()}
        return x

    return strip_dates(req)


def _run(engine: str, monkeypatch: pytest.MonkeyPatch, script, *, tools=(), **kwargs) -> tuple:
    client = ScriptedClient(script)
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: client)
    host = FakeHost(tools=[EchoTool(t.name) if isinstance(t, EchoTool) else t for t in tools])
    sink: Dict[str, Any] = {}
    params = dict(
        text="hello",
        provider="openai",
        workflow_id="wf-1",
        workflow_name="wf",
        user_id="u1",
        interaction_id="inter-1",
        memory_distill=False,
        enable_memory=False,
        usage_sink=sink,
    )
    params.update(kwargs)
    executor = AgentTurnExecutor() if engine == "geny" else GenyRSITurnExecutor()
    out = executor.run(host, **params)
    chunks = list(out) if not isinstance(out, str) else out
    return chunks, sink, client


def _both(monkeypatch, script, **kw):
    a = _run("geny", monkeypatch, script, **kw)
    b = _run("geny-rsi", monkeypatch, script, **kw)
    return a, b


def test_plain_text_stream_is_identical(monkeypatch):
    (ca, sa, cla), (cb, sb, clb) = _both(monkeypatch, [text_step("안녕하세요, 무엇을 도와드릴까요?")])
    assert _norm(ca) == _norm(cb)
    assert sa == sb
    assert [_norm_request(r) for r in cla.requests] == [_norm_request(r) for r in clb.requests]


def test_tool_turn_stream_is_identical(monkeypatch):
    script = [
        tool_step("확인해 볼게요.", [("t1", "echo", {"text": "x"})]),
        text_step("결과는 echo:x 입니다."),
    ]
    (ca, sa, cla), (cb, sb, clb) = _both(monkeypatch, script, tools=[EchoTool()])
    assert _norm(ca) == _norm(cb)
    assert sa == sb
    assert [_norm_request(r) for r in cla.requests] == [_norm_request(r) for r in clb.requests]
    kinds = [c["data"]["type"] for c in cb if isinstance(c, dict) and c["type"] == "agent_event"]
    assert kinds == ["tool_call", "tool_result"]
    assert cb[-1]["type"] == "usage" and cb[-1]["data"]["calls"] == 2


def test_tool_error_stream_is_identical(monkeypatch):
    script = [tool_step("", [("t1", "echo", {"text": "x"})]), text_step("실패했네요.")]
    (ca, sa, _), (cb, sb, _) = _both(monkeypatch, script, tools=[EchoTool(fail=True)])
    assert _norm(ca) == _norm(cb)
    assert sa == sb


def test_non_streaming_text_is_identical(monkeypatch):
    (ra, sa, _), (rb, sb, _) = _both(monkeypatch, [text_step("완료했습니다.")], streaming=False)
    assert ra == rb == "완료했습니다."
    assert sa == sb


def test_non_streaming_tool_turn_is_identical(monkeypatch):
    script = [tool_step("", [("t1", "echo", {"text": "y"})]), text_step("echo:y 확인")]
    (ra, sa, cla), (rb, sb, clb) = _both(monkeypatch, script, tools=[EchoTool()], streaming=False)
    assert ra == rb
    assert sa == sb
    assert len(cla.requests) == len(clb.requests) == 2


def test_structured_output_settles_identically(monkeypatch):
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
    script = [text_step('```json\n{"answer": "42"}\n```')]
    (ra, sa, cla), (rb, sb, clb) = _both(monkeypatch, script, streaming=False, output_schema=schema)
    assert ra == rb == '{"answer": "42"}'
    assert [_norm_request(r) for r in cla.requests] == [_norm_request(r) for r in clb.requests]


def test_slice_limit_continuation_and_suspend_are_identical(monkeypatch):
    # 매 응답이 도구를 부른다 → max_iterations=1 이면 슬라이스마다 중단, 이어가기 1회 후 멈춤 안내.
    script = [tool_step("", [("t1", "echo", {"text": "loop"})])]
    (ca, sa, _), (cb, sb, _) = _both(
        monkeypatch, script, tools=[EchoTool()], max_iterations=1, max_continuation_slices=1
    )
    assert _norm(ca) == _norm(cb)
    assert sa == sb
    types = [c["data"]["type"] for c in cb if isinstance(c, dict) and c["type"] == "agent_event"]
    assert "task_progress" in types and "task_suspended" in types


def test_usage_payload_shape_matches_contract(monkeypatch):
    script = [tool_step("a", [("t1", "echo", {"text": "q"})], usage=(100, 7)), text_step("b", usage=(150, 9))]
    _, (cb, sb, _) = _both(monkeypatch, script, tools=[EchoTool()])
    usage = cb[-1]["data"]
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "total_cost_usd",
        "model",
        "provider",
        "calls",
        "first_call_prompt_tokens",
        "max_call_prompt_tokens",
    ):
        assert key in usage
    assert usage["input_tokens"] == 250 and usage["output_tokens"] == 16
    assert usage["calls"] == 2 and usage["first_call_prompt_tokens"] == 100 and usage["max_call_prompt_tokens"] == 150
    assert sb == usage


def test_close_mid_stream_fills_sink_partial_and_tears_down(monkeypatch):
    script = [tool_step("생각 중", [("t1", "echo", {"text": "x"})]), text_step("끝")]
    client = ScriptedClient(script)
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: client)
    host = FakeHost(tools=[EchoTool()])
    sink: Dict[str, Any] = {}
    gen = GenyRSITurnExecutor().run(
        host,
        text="hi",
        provider="openai",
        workflow_id="wf-1",
        user_id="u1",
        interaction_id="i-close",
        enable_memory=False,
        memory_distill=False,
        usage_sink=sink,
    )
    first = next(gen)
    assert isinstance(first, str)
    gen.close()
    assert sink.get("partial") is True
    assert host.finalized == 1


class _RecordingMemory:
    """STM 만 흉내 — record_turn 으로 받은 것을 쌓고, 나머지 호출은 빈 값으로 답한다."""

    def __init__(self) -> None:
        self.turns: list = []

    async def record_turn(self, turn: Any) -> None:
        self.turns.append(turn)

    async def close(self) -> None:
        return None

    def __getattr__(self, name: str):
        async def _noop(*a: Any, **k: Any) -> Any:
            return None

        return _noop


@pytest.mark.parametrize("streaming", [True, False])
def test_unfinished_turn_memory_is_identical(monkeypatch, streaming):
    """끝나지 못한 턴(오류)의 질문·한 일·표식이 두 엔진에서 같게 단기 기억에 남는다(런타임 4.77.0)."""
    recorded = {}
    for engine in ("geny", "geny-rsi"):
        mem = _RecordingMemory()
        monkeypatch.setattr(FakeHost, "build_memory_provider", lambda self, *a, **k: mem)
        _run(engine, monkeypatch, [tool_step("", [("t1", "echo", {"text": "x"})]), error_step("provider down")],
             tools=[EchoTool("echo")], enable_memory=True, streaming=streaming)
        recorded[engine] = [(t.role, str(t.content)) for t in mem.turns]
    assert recorded["geny"] == recorded["geny-rsi"]
    assert recorded["geny-rsi"] and "ended with an error" in recorded["geny-rsi"][-1][1]
