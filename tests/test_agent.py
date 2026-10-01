"""GenyRSI — PipelinePresets 와 같은 사용감, 운영과 같은 턴 경로, 두 엔진 A/B."""

from __future__ import annotations

import asyncio

from xgen_agent_runtime.host import runner as runner_mod

from tests.kernel.fakes import EchoTool, ScriptedClient, text_step, tool_step
from xgen_rsi import GenyRSI


def _client(script, seen=None):
    def build(*a, **k):
        c = ScriptedClient(script)
        if seen is not None:
            seen.append(c)
        return c

    return build


def test_minimal_run_and_async(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_client", _client([text_step("Paris")]))
    agent = GenyRSI.minimal(provider="openai", model="m", api_key="k", record_dir=str(tmp_path / "rec"))
    r = agent.run_sync("capital of France?")
    assert r.text == "Paris" and r.engine == "geny-rsi" and r.input_tokens == 10 and r.output_tokens == 5
    assert r.harness.startswith("h0@sha256:") and r.record["status"] == "completed"
    r2 = asyncio.run(agent.run("again"))
    assert r2.text == "Paris" and agent.history == []  # minimal 은 이력을 남기지 않는다


def test_chat_keeps_history(monkeypatch):
    seen = []
    monkeypatch.setattr(runner_mod, "build_client", _client([text_step("hi there")], seen))
    agent = GenyRSI.chat(provider="openai", model="m", api_key="k", system_prompt="Be brief.")
    agent.run_sync("hello")
    agent.run_sync("and again")
    assert len(agent.history) == 4
    msgs = seen[-1].requests[0]["messages"]
    texts = [m["content"] if isinstance(m["content"], str) else m["content"][0].get("text") for m in msgs]
    assert texts[0] == "hello" and texts[1] == "hi there"
    assert "Be brief." in str(seen[-1].requests[0]["system"])


def test_agent_tools_workspace_and_stream(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    script = [tool_step("", [("w1", "Write", {"file_path": str(ws / "a.txt"), "content": "x"})]),
              tool_step("", [("e1", "echo", {"text": "q"})]), text_step("done!")]
    monkeypatch.setattr(runner_mod, "build_client", _client(script))
    agent = GenyRSI.agent(provider="openai", model="m", api_key="k", workspace=str(ws), tools=[EchoTool("echo")])
    out = "".join(agent.stream_sync("write a.txt"))
    assert out.endswith("done!") and (ws / "a.txt").read_text() == "x"
    assert agent.last_result is not None and agent.last_result.usage.get("calls") == 3


def test_same_call_on_both_engines(monkeypatch, tmp_path):
    texts = {}
    for engine in ("geny", "geny-rsi"):
        monkeypatch.setattr(runner_mod, "build_client", _client([text_step("same answer")]))
        texts[engine] = GenyRSI.minimal(provider="openai", model="m", api_key="k", engine=engine).run_sync("q")
    assert texts["geny"].text == texts["geny-rsi"].text == "same answer"
    assert texts["geny"].usage == texts["geny-rsi"].usage
    assert texts["geny"].harness is None and texts["geny-rsi"].harness.startswith("h0@")


def test_async_stream(monkeypatch):
    monkeypatch.setattr(runner_mod, "build_client", _client([text_step("streamed text")]))
    agent = GenyRSI.minimal(provider="openai", model="m", api_key="k")

    async def collect():
        return [c async for c in agent.stream("q")]

    assert "".join(asyncio.run(collect())) == "streamed text"


def test_default_model_and_validation():
    assert GenyRSI.minimal(provider="anthropic", api_key="k").model == "claude-sonnet-5"
    assert GenyRSI.minimal(provider="openai", api_key="k").model == "gpt-6-sol"
    try:
        GenyRSI(provider="openai", model="m", engine="nope")
    except ValueError:
        pass
    else:
        raise AssertionError("bad engine accepted")
