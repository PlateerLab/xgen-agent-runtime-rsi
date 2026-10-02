"""턴 세계 기록과 재생(설계 41) — 같은 하네스·같은 모델 응답이면 재생이 실제 턴과 같은 요청을 보내고, 도구는 다시 돌지 않는다."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from tests.kernel.fakes import EchoTool, FakeHost, ScriptedClient, text_step, tool_step
from xgen_rsi.base.host import runner as runner_mod
from xgen_rsi.consolidate.replay import NO_RECORD, CallBook, replay_world
from xgen_rsi.consolidate.world import TurnWorld
from xgen_rsi.evolve.runner import PolicySpec
from xgen_rsi.harness.spec import load_manifest
from xgen_rsi.kernel.executor import BUILTIN_H0
from xgen_rsi.turn_executor import GenyRSITurnExecutor

POLICY = PolicySpec(provider="openai", model="fake-model", api_key="k")
HISTORY = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"}]


def _live(monkeypatch, script, tools, *, settings=None, text="what is echo of hi?", **kw):
    client = ScriptedClient(script)
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: client)
    records: List[Dict[str, Any]] = []
    host = FakeHost(settings={"XGEN_RSI_RECORD_WORLD": "1", **(settings or {})}, tools=tools)
    host.rsi_record = records.append  # type: ignore[attr-defined]
    params = dict(text=text, provider="openai", workflow_id="wf", user_id="u", interaction_id="i-1",
                  enable_memory=False, memory_distill=False, streaming=False, memory=list(HISTORY))
    params.update(kw)
    out = GenyRSITurnExecutor().run(host, **params)
    assert records, "the host hook received no record"
    return out, client, records[-1]


def _strip_volatile(req: Dict[str, Any]) -> str:
    return json.dumps(req, ensure_ascii=False, sort_keys=True, default=str)


def test_world_records_inputs_tool_results_and_outcome(monkeypatch):
    echo = EchoTool()
    out, _, rec = _live(monkeypatch, [tool_step("", [("t1", "echo", {"text": "hi"})]), text_step("echo of hi is echo:hi")], [echo])
    assert out == "echo of hi is echo:hi"
    world = rec["world"]
    assert world["schema"] == "xgen-rsi-world/1" and world["replayable"] is True
    assert world["plan"]["system_parts"], "system parts (the harness's raw material) are recorded"
    assert [m["content"] for m in world["state"]["messages"]] == ["earlier question", "earlier answer"]
    assert world["input"] == "what is echo of hi?"
    names = {d["name"] for d in world["tools"]["defs"]}
    assert "echo" in names
    assert world["tools"]["calls"] == [{"name": "echo", "input": {"text": "hi"}, "content": "echo:hi", "is_error": False}]
    assert world["outcome"]["final_text"] == "echo of hi is echo:hi"
    assert world["outcome"]["status"] == "completed"
    assert "api_key" not in json.dumps(world["plan"]) and "credentials" not in world["plan"]["pipeline_kwargs"]


def test_no_world_unless_the_host_asks(monkeypatch):
    client = ScriptedClient([text_step("ok")])
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: client)
    records: List[Dict[str, Any]] = []
    host = FakeHost(settings={})
    host.rsi_record = records.append  # type: ignore[attr-defined]
    GenyRSITurnExecutor().run(host, text="hi", provider="openai", workflow_id="wf", user_id="u", interaction_id="i-1",
                              enable_memory=False, memory_distill=False, streaming=False)
    assert records and records[-1].get("world") is None


def test_replay_with_the_same_harness_sends_the_same_requests_and_never_runs_tools(monkeypatch):
    script = [tool_step("let me check", [("t1", "echo", {"text": "hi"})]), text_step("echo of hi is echo:hi")]
    echo = EchoTool()
    _, live_client, rec = _live(monkeypatch, script, [echo])
    assert len(echo.calls) == 1
    replay_client = ScriptedClient(script)
    world = TurnWorld(id="io-1", data=rec["world"])
    res = replay_world(world, BUILTIN_H0, POLICY, client_factory=lambda plan: replay_client)
    assert res.error == "", res.error
    assert res.answer == "echo of hi is echo:hi"
    assert res.off_support == 0 and res.answered == 1
    assert len(echo.calls) == 1, "replay returned the recorded result instead of running the tool"
    assert len(replay_client.requests) == len(live_client.requests) == 2
    for live, again in zip(live_client.requests, replay_client.requests):
        assert _strip_volatile(again["messages"]) == _strip_volatile(live["messages"])
        assert _strip_volatile(again["tools"]) == _strip_volatile(live["tools"])
        assert _strip_volatile(again["system"]) == _strip_volatile(live["system"])
    assert res.policy_tokens > 0 and res.params_read, "the replay is measured like a turn (c(τ), params read)"


def test_call_outside_the_recorded_session_gets_no_result(monkeypatch):
    echo = EchoTool()
    _, _, rec = _live(monkeypatch, [tool_step("", [("t1", "echo", {"text": "hi"})]), text_step("done")], [echo])
    other = ScriptedClient([tool_step("", [("t9", "echo", {"text": "something else"})]), text_step("could not")])
    res = replay_world(TurnWorld(id="io-1", data=rec["world"]), BUILTIN_H0, POLICY, client_factory=lambda plan: other)
    assert res.off_support == 1
    assert len(echo.calls) == 1
    tool_results = [b for m in other.requests[1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                    for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert any(NO_RECORD in json.dumps(b, ensure_ascii=False) for b in tool_results)


def test_candidate_harness_changes_what_the_model_sees_in_replay(monkeypatch, tmp_path):
    _, _, rec = _live(monkeypatch, [text_step("plain answer")], [])
    cand = tmp_path / "cand"
    m = load_manifest(BUILTIN_H0)
    import shutil

    shutil.copytree(BUILTIN_H0, cand)
    raw = json.loads((cand / "manifest.json").read_text())
    for c in raw["components"]:
        if c["id"] == "prompt.system":
            c["params"]["extra_blocks"] = [{"id": "lead", "text": "Lead with a one-line summary."}]
    (cand / "manifest.json").write_text(json.dumps(raw))
    client = ScriptedClient([text_step("summary first")])
    res = replay_world(TurnWorld(id="io-1", data=rec["world"]), cand, POLICY, client_factory=lambda plan: client)
    assert res.answer == "summary first"
    assert "Lead with a one-line summary." in json.dumps(client.requests[0]["system"])
    assert any(a.startswith("prompt.system.params.extra_blocks") for a in res.params_read)
    assert m.version_id() != load_manifest(cand).version_id()


def test_callbook_matches_loosely_and_repeats_the_last_result():
    book = CallBook([{"name": "Read", "input": {"path": "/a.txt"}, "content": "A", "is_error": False},
                     {"name": "Read", "input": {"path": "/a.txt"}, "content": "A2", "is_error": False}])
    assert book.answer("Read", {"path": "/a.txt"})[0] == "A"
    assert book.answer("Read", {"path": "/a.txt"})[0] == "A2"
    assert book.answer("Read", {"path": "/a.txt"})[0] == "A2"
    assert book.answer("Read", {"path": " /A.txt "})[2] is True  # 느슨한 비교: 공백·대소문자만 무시
    assert book.answer("Read", {"path": "/b.txt"})[2] is False
    assert book.answer("Search", {"q": "x"}) == (NO_RECORD, True, False)


def test_world_too_large_is_kept_but_not_replayable():
    from xgen_rsi.kernel.capture import WorldCapture

    cap = WorldCapture(max_world_bytes=200)

    class _State:
        messages = [{"role": "user", "content": "x" * 1000}]
        metadata: Dict[str, Any] = {}
        shared: Dict[str, Any] = {}
        session_id = "s"

    class _Plan:
        state = _State()
        pipeline_kwargs: Dict[str, Any] = {}
        interaction_id = "i"
        model = "m"
        provider = "p"
        node_name = "n"
        system_prompt = "sp"
        system_parts = [("base", "sp")]
        schema = None
        budget_window = 0
        enable_compaction = True
        max_tokens = 100
        kwargs: Dict[str, Any] = {}
        pipeline_input = "hi"
        memory_provider = None

    cap.on_prepare(_Plan(), registry=None, contributed=[], harness_version="v", lineage="l", provider="p")
    world = cap.finish(state=_State(), final_text="a", status="completed", termination_reason="", policy_tokens=1, steps={})
    assert world["replayable"] is False and world["state"]["messages"] == []
    assert not TurnWorld(id="x", data=world).replayable


@pytest.mark.parametrize("content", ["text", [{"type": "text", "text": "a"}, {"type": "image", "source": {"data": "xx"}}]])
def test_images_become_placeholders(content):
    from xgen_rsi.kernel.capture import IMAGE_PLACEHOLDER, WorldCapture

    cap = WorldCapture()
    out = cap._content(content)
    if isinstance(content, list):
        assert out[1] == {"type": "text", "text": IMAGE_PLACEHOLDER} and cap.world["images_dropped"]
    else:
        assert out == "text"


def test_replay_answers_memory_retrieval_from_the_world(monkeypatch):
    _, _, rec = _live(monkeypatch, [text_step("ok")], [])
    data = dict(rec["world"])
    data["memory"] = {"present": True, "chunks": [{"key": "pref", "source": "notes", "content": "the user prefers tables",
                                                  "relevance_score": 0.9, "metadata": {}}]}
    client = ScriptedClient([text_step("ok")])
    res = replay_world(TurnWorld(id="io-1", data=data), BUILTIN_H0, POLICY, client_factory=lambda plan: client)
    assert res.error == "", res.error
    assert "the user prefers tables" in json.dumps(client.requests[0], ensure_ascii=False)
