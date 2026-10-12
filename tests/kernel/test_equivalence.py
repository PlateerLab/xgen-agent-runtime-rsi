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

from tests.kernel.fakes import (
    EchoTool,
    FakeHost,
    ScriptedCLIClient,
    ScriptedClient,
    error_step,
    overflow_step,
    text_step,
    tool_step,
)
from xgen_rsi.base.host import runner as runner_mod
from xgen_rsi.base.host.turn_executor import AgentTurnExecutor
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


def _run(
    engine: str,
    monkeypatch: pytest.MonkeyPatch,
    script,
    *,
    tools=(),
    host_cls=FakeHost,
    client_cls=ScriptedClient,
    **kwargs,
) -> tuple:
    client = client_cls(script)
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: client)
    host = host_cls(tools=[EchoTool(t.name) if isinstance(t, EchoTool) else t for t in tools])
    if client_cls is ScriptedCLIClient:
        # CLI 백엔드는 호스트가 클라이언트를 짓는다(두 엔진 모두 같은 훅을 지난다).
        monkeypatch.setattr(host, "build_cli_runtime", lambda provider, params: (client, None), raising=False)
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


# ── 4.89 압축 정책: 두 엔진이 같은 때에 같은 것을 줄이고 같은 요청을 보낸다 ─────────────────────

_KO = "한국어로 길게 쓴 지난 대화 문장입니다. "


def _history(turns: int, reps: int) -> list:
    """한국어 지난 대화(질문·답 쌍). 4.89 추정은 한국어를 글자 부류로 세므로 크기를 가늠하기 쉽다."""
    out = []
    for i in range(turns):
        out.append({"role": "user", "content": f"{i}번째 질문: " + _KO * reps})
        out.append({"role": "assistant", "content": f"{i}번째 답: " + _KO * reps})
    return out


@pytest.fixture
def rsi_state(monkeypatch):
    """geny-rsi 쪽 턴 기록(PipelineState)을 붙잡는다. 사건(``context.overflow`` 등)과 창 교정을 확인할 때 쓴다."""
    from xgen_rsi.kernel.executor import RSITurnExecutor

    seen: list = []
    original = RSITurnExecutor.prepare

    def _prepare(self, plan, host):
        seen.append(plan.state)
        return original(self, plan, host)

    monkeypatch.setattr(RSITurnExecutor, "prepare", _prepare)
    return seen


def _event_types(state) -> list:
    return [e.get("type") for e in state.events if isinstance(e, dict)]


def _same(a, b) -> None:
    (ca, sa, cla), (cb, sb, clb) = a, b
    if isinstance(ca, str) or isinstance(cb, str):
        assert ca == cb
    else:
        assert _norm(ca) == _norm(cb)
    assert sa == sb
    assert [_norm_request(r) for r in cla.requests] == [_norm_request(r) for r in clb.requests]


def _main_and_compact(client) -> list:
    """호출 순서를 엔진과 무관한 이름으로(메인 호출 purpose 는 엔진마다 다르다: api / main)."""
    return ["compact" if "compact" in p else "main" for p in client.purposes]


def _text(chunks) -> str:
    return chunks if isinstance(chunks, str) else "".join(c for c in chunks if isinstance(c, str))


@pytest.mark.parametrize("streaming", [True, False])
def test_proactive_compaction_with_a_tool_turn_is_identical(monkeypatch, rsi_state, streaming):
    """지난 대화가 입력 예산의 80% 를 넘으면 첫 호출 전에 같은 요약을 하고, 도구 턴이 같게 이어진다."""
    script = [
        text_step("요약입니다."),
        tool_step("확인해 볼게요.", [("t1", "echo", {"text": "x"})]),
        text_step("결과는 echo:x 입니다."),
    ]
    a, b = _both(
        monkeypatch, script, tools=[EchoTool()], memory=_history(6, 60), context_window=16000,
        max_tokens=256, streaming=streaming,
    )
    _same(a, b)
    assert _main_and_compact(a[2]) == _main_and_compact(b[2]) == ["compact", "main", "main"]
    assert "context.compacted" in _event_types(rsi_state[-1])
    assert b[1]["auxiliary"]["by_purpose"]["compact"]["calls"] == 1


@pytest.mark.parametrize("streaming", [True, False])
def test_provider_count_anchors_the_next_compaction_identically(monkeypatch, streaming):
    """공급자가 센 입력 토큰(기준점)이 다음 반복의 압축 판단을 정한다. 추정만으로는 문턱 아래인 대화다."""
    script = [
        tool_step("확인해 볼게요.", [("t1", "echo", {"text": "x"})], usage=(15000, 5)),
        text_step("요약입니다."),
        text_step("끝입니다."),
    ]
    a, b = _both(
        monkeypatch, script, tools=[EchoTool()], memory=_history(4, 60), context_window=16000,
        max_tokens=256, streaming=streaming,
    )
    _same(a, b)
    assert _main_and_compact(a[2]) == _main_and_compact(b[2]) == ["main", "compact", "main"]


@pytest.mark.parametrize("streaming", [True, False])
def test_overflow_then_retry_is_identical(monkeypatch, rsi_state, streaming):
    """공급자가 창 넘침으로 거절하면 강하게 압축하고 한 번 다시 보낸다(main → compact → main)."""
    script = [overflow_step("prompt is too long"), text_step("요약입니다."), text_step("답입니다.")]
    a, b = _both(monkeypatch, script, memory=_history(6, 60), context_window=32000, streaming=streaming)
    _same(a, b)
    assert _main_and_compact(a[2]) == _main_and_compact(b[2]) == ["main", "compact", "main"]
    assert _text(b[0]).endswith("답입니다.")
    assert _event_types(rsi_state[-1]).count("context.overflow") == 1


@pytest.mark.parametrize("streaming", [True, False])
def test_overflow_twice_propagates_identically(monkeypatch, streaming):
    """다시 보낸 요청도 거절되면 그 오류로 턴이 끝난다(재시도는 한 번뿐)."""
    script = [overflow_step("prompt is too long"), text_step("요약입니다."), overflow_step("prompt is too long")]
    a, b = _both(monkeypatch, script, memory=_history(6, 60), context_window=32000, streaming=streaming)
    _same(a, b)
    assert _main_and_compact(b[2]) == ["main", "compact", "main"]
    assert "[ERROR] prompt is too long" in _text(b[0])


@pytest.mark.parametrize("streaming", [True, False])
def test_provider_limit_below_the_window_is_identical(monkeypatch, rsi_state, streaming):
    """오류에 적힌 한도가 믿던 창보다 작으면 그 턴의 창을 그 값으로 바꾸고 줄인다."""
    script = [
        overflow_step("prompt is too long: 40000 tokens > 30000 maximum"),
        text_step("요약입니다."),
        text_step("답입니다."),
    ]
    a, b = _both(monkeypatch, script, memory=_history(6, 60), context_window=64000, streaming=streaming)
    _same(a, b)
    assert _main_and_compact(b[2]) == ["main", "compact", "main"]
    assert rsi_state[-1].context_window_budget == 30000


@pytest.mark.parametrize("streaming", [True, False])
def test_small_reported_prompt_skips_compaction_identically(monkeypatch, rsi_state, streaming):
    """메시지는 작고 답 몫 때문에 넘친 거절(vLLM)은 압축하지 않고 그대로 올린다."""
    message = (
        "This model's maximum context length is 8192 tokens. However, you requested 8292 tokens "
        "(100 in the messages, 8192 in the completion)."
    )
    a, b = _both(
        monkeypatch, [overflow_step(message), text_step("never")], memory=_history(6, 60),
        context_window=32000, streaming=streaming,
    )
    _same(a, b)
    assert len(a[2].requests) == len(b[2].requests) == 1
    assert "[ERROR]" in _text(b[0])
    assert "context.compacted" not in _event_types(rsi_state[-1])


@pytest.mark.parametrize("streaming", [True, False])
def test_guard_reject_is_identical(monkeypatch, streaming):
    """지금 질문만으로 창이 모자라면(압축으로 줄일 수 없다) 두 엔진이 같은 가드 오류로 끝난다."""
    a, b = _both(
        monkeypatch, [text_step("never")], text="질문 " * 20000, context_window=2048, max_tokens=1024,
        streaming=streaming,
    )
    _same(a, b)
    assert a[2].requests == b[2].requests == []
    assert "[ERROR] Context near limit" in _text(b[0])


@pytest.mark.parametrize("streaming", [True, False])
def test_guard_hard_compaction_is_identical(monkeypatch, rsi_state, streaming):
    """약한 압박의 요약이 실패해(빈 응답 두 번) 이력이 그대로면 가드가 강하게 압축하고 다시 잰다."""
    script = [text_step(""), text_step(""), text_step("요약입니다."), text_step("답입니다.")]
    a, b = _both(
        monkeypatch, script, memory=_history(6, 90), context_window=16000, max_tokens=256,
        streaming=streaming,
    )
    _same(a, b)
    assert _main_and_compact(b[2]) == ["compact", "compact", "compact", "main"]
    state = rsi_state[-1]
    compacted = [e["data"] for e in state.events if e.get("type") == "context.compacted"]
    assert "guard.compacting" in _event_types(state)
    assert [d["trigger"] for d in compacted] == ["guard"]


_OLDER = "[이전 대화 기록 - 참고용]\n사용자: 예전 질문\n어시스턴트: 예전 답"


@pytest.mark.parametrize("streaming", [True, False])
def test_memory_node_history_context_is_identical(monkeypatch, streaming):
    """메모리 노드의 이전 대화 문자열은 API 백엔드에서 시스템 프롬프트 끝에 붙는다."""
    a, b = _both(monkeypatch, [text_step("답")], memory=(_history(1, 2), _OLDER), streaming=streaming)
    _same(a, b)
    assert b[2].requests[0]["system"].endswith("\n\n" + _OLDER)


@pytest.mark.parametrize("streaming", [True, False])
def test_trimmed_history_context_is_identical(monkeypatch, streaming):
    """긴 이전 대화 문자열은 창의 몫 안으로 줄이되 머리글과 가장 최근 대화를 남긴다."""
    older = "[이전 대화 기록 - 참고용]\n" + _KO * 4000 + "가장 최근 대화"
    a, b = _both(
        monkeypatch, [text_step("답")], memory=(_history(1, 2), older), context_window=16000,
        streaming=streaming,
    )
    _same(a, b)
    system = b[2].requests[0]["system"]
    assert "더 오래된 대화 생략" in system and system.endswith("가장 최근 대화")
    assert len(system) < len(older)


@pytest.mark.parametrize("provider", ["openai", "claude_code"])
def test_history_context_goes_to_the_system_prompt_or_the_turn_notes(monkeypatch, provider):
    """API 백엔드는 시스템 프롬프트 끝, CLI 백엔드는 이번 턴 안내(요청 사본의 session-context)."""
    client_cls = ScriptedCLIClient if provider == "claude_code" else ScriptedClient
    older = "[이전 대화 기록 - 참고용]\n사용자: 예전 질문"
    a, b = _both(
        monkeypatch, [text_step("답")], client_cls=client_cls, provider=provider,
        memory=(_history(1, 2), older),
    )
    _same(a, b)
    request = b[2].requests[0]
    last_user = request["messages"][-1]["content"]
    in_notes = any(older in str(block.get("text", "")) for block in last_user if isinstance(block, dict))
    if provider == "claude_code":
        assert older not in request["system"] and in_notes
    else:
        assert request["system"].endswith(older) and not in_notes


def test_cli_history_is_trimmed_identically(monkeypatch, rsi_state):
    """CLI 백엔드는 런타임 압축이 없다: 넘치면 가장 오래된 이력부터 빼고, 기록 워터마크도 같이 내린다."""
    from xgen_rsi.base.host.conversation_archive import _ARCHIVED_KEY, STM_RECORDED_KEY

    history = _history(20, 60)
    a, b = _both(
        monkeypatch, [text_step("답")], client_cls=ScriptedCLIClient, provider="claude_code",
        memory=history, context_window=64000, max_tokens=32000,
    )
    _same(a, b)
    sent = len(b[2].requests[0]["messages"])
    assert 1 < sent < len(history)
    metadata = rsi_state[-1].metadata
    assert metadata[STM_RECORDED_KEY] == metadata[_ARCHIVED_KEY] == sent - 1
    assert not any("[안내:" in c for c in b[0] if isinstance(c, str))  # 이력만 줄면 사용자 안내는 없다


class _NotesHost(FakeHost):
    def turn_notes(self):
        return ["이번 질문에 고른 기억: 사용자는 표로 답받기를 좋아한다."]


def test_host_turn_notes_are_identical(monkeypatch):
    """호스트의 ``turn_notes`` 훅(4.88)이 준 글은 두 엔진에서 같은 자리(이번 턴 안내)에 실린다."""
    a, b = _both(monkeypatch, [text_step("답")], host_cls=_NotesHost)
    _same(a, b)
    last_user = b[2].requests[0]["messages"][-1]["content"]
    assert any("표로 답받기를 좋아한다" in str(block.get("text", "")) for block in last_user)


class _UserPcHost(FakeHost):
    """연결 폴더 하나를 주는 호스트(4.85 ``user_pc_connection`` 훅). 기기 호출은 쓰지 않는다."""

    def user_pc_connection(self):
        from xgen_rsi.base.host.user_pc import PcFolder, UserPcConnection

        async def _call(folder, action, args):
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

        folder = PcFolder(name="docs", path="/home/me/docs", device_id="d1", device_name="my-pc",
                          platform="linux", shell="bash")
        return UserPcConnection(folders=[folder], call=_call)


def test_user_pc_connection_is_identical(monkeypatch):
    """호스트가 기기 접속을 주면 연결 폴더는 UserPc 하나로 닿고, 그 사실이 이번 턴 안내에 실린다."""
    a, b = _both(monkeypatch, [text_step("답")], host_cls=_UserPcHost)
    _same(a, b)
    request = b[2].requests[0]
    assert "UserPc" in [t["name"] for t in request["tools"]]
    last_user = request["messages"][-1]["content"]
    assert any("/home/me/docs" in str(block.get("text", "")) for block in last_user)


@pytest.mark.parametrize("streaming", [True, False])
def test_messages_compacted_mid_turn_reach_memory_identically(monkeypatch, rsi_state, streaming):
    """턴 중간 요약이 기록 전의 메시지(이번 질문 포함)를 이력에서 빼도 단기 기억에는 같은 순서로 남는다."""
    big = _KO * 250
    script = [
        tool_step("", [("t1", "echo", {"text": "가" + big})]),
        tool_step("", [("t2", "echo", {"text": "나" + big})]),
        text_step("요약입니다."),
        text_step("끝"),
    ]
    recorded = {}
    clients = {}
    for engine in ("geny", "geny-rsi"):
        mem = _RecordingMemory()
        monkeypatch.setattr(FakeHost, "build_memory_provider", lambda self, *a, **k: mem)
        _, _, clients[engine] = _run(
            engine, monkeypatch, script, tools=[EchoTool()], enable_memory=True, context_window=16000,
            max_tokens=256, streaming=streaming,
        )
        recorded[engine] = [(t.role, str(t.content)) for t in mem.turns]
    assert [_norm_request(r) for r in clients["geny"].requests] == [
        _norm_request(r) for r in clients["geny-rsi"].requests
    ]
    assert _main_and_compact(clients["geny-rsi"]) == ["main", "main", "compact", "main"]
    assert recorded["geny"] == recorded["geny-rsi"]
    assert recorded["geny-rsi"][0] == ("user", "hello")
    first = rsi_state[-1].messages[0]["content"]
    assert "[Context checkpoint]" in (first if isinstance(first, str) else str(first))
