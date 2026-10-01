"""RSI 커널 고유 성질 — 원장(모든 정책 호출), 하네스 편집 반영, 계보, rollout, CLI 경로, 가드."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from tests.kernel.fakes import EchoTool, FakeHost, ScriptedClient, text_step, tool_step
from xgen_rsi.base.host import runner as runner_mod
from xgen_rsi.harness.spec import load_manifest, save_manifest
from xgen_rsi.kernel.executor import BUILTIN_H0
from xgen_rsi.turn_executor import GenyRSITurnExecutor


def _go(monkeypatch, client, host, **kw):
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: client)
    params = dict(
        text="hi",
        provider="openai",
        workflow_id="wf",
        user_id="u",
        interaction_id="i-1",
        enable_memory=False,
        memory_distill=False,
    )
    params.update(kw)
    out = GenyRSITurnExecutor().run(host, **params)
    return list(out) if not isinstance(out, str) else out


def _records(d: Path):
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]


def test_compaction_calls_reach_the_ledger_but_not_external_usage(monkeypatch, tmp_path):
    """요약 압축 호출은 c(τ)(내부 원장)에 들어가고 외부 usage(과금 의미)에는 들어가지 않는다(결정 D-7)."""
    long_history = [{"role": "user", "content": "x" * 4000}, {"role": "assistant", "content": "y" * 4000}] * 6
    client = ScriptedClient([text_step("요약입니다."), text_step("최종 답")])
    host = FakeHost(settings={"XGEN_RSI_RECORD_DIR": str(tmp_path)})
    sink: Dict[str, Any] = {}
    chunks = _go(
        monkeypatch,
        client,
        host,
        memory=long_history,
        context_window=16000,
        max_tokens=256,
        usage_sink=sink,
    )
    rec = _records(tmp_path)[0]
    purposes = rec["usage_by_purpose"]
    assert "compact" in purposes, purposes
    assert purposes["main"]["calls"] >= 1
    # 외부 usage 는 메인 호출만
    assert sink["calls"] == purposes["main"]["calls"]
    assert rec["policy_tokens"] > sink["input_tokens"] + sink["output_tokens"] - 1
    assert chunks[-1]["type"] == "usage"


def test_prompt_part_override_changes_the_request(monkeypatch, tmp_path):
    """하네스 편집(prompt 조각 교체·추가)이 모델 요청에 그대로 반영된다 — 원자 편집 주소의 의미."""
    m = load_manifest(BUILTIN_H0)
    m = m.with_param("prompt.system.params.extra_blocks", [{"id": "verify", "text": "Before finishing, re-read the request."}])
    m = m.with_param("prompt.system.params.part_overrides", {"efficiency": None})
    hdir = tmp_path / "h1"
    save_manifest(m, hdir)
    client = ScriptedClient([text_step("ok")])
    host = FakeHost(settings={"XGEN_RSI_HARNESS_DIR": str(hdir)}, tools=[EchoTool()])
    _go(monkeypatch, client, host)
    system = client.requests[0]["system"]
    system_text = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False)
    assert "Before finishing, re-read the request." in system_text

    from xgen_rsi.base.host._constants import EFFICIENCY_PROMPT_BLOCK

    assert EFFICIENCY_PROMPT_BLOCK.strip()[:40] not in system_text


def test_lineage_table_routes_by_policy(monkeypatch, tmp_path):
    h_default = tmp_path / "default"
    h_openai = tmp_path / "openai"
    m = load_manifest(BUILTIN_H0)
    save_manifest(m, h_default)
    save_manifest(m.with_param("prompt.system.params.extra_blocks", [{"id": "o", "text": "OPENAI-LINEAGE"}]), h_openai)
    table = tmp_path / "lineage.json"
    table.write_text(json.dumps({"lineages": {"default": "default", "openai": "openai"}}))
    client = ScriptedClient([text_step("ok")])
    host = FakeHost(settings={"XGEN_RSI_LINEAGE_FILE": str(table), "XGEN_RSI_RECORD_DIR": str(tmp_path / "rec")})
    _go(monkeypatch, client, host)
    system = client.requests[0]["system"]
    assert "OPENAI-LINEAGE" in (system if isinstance(system, str) else json.dumps(system, ensure_ascii=False))
    assert _records(tmp_path / "rec")[0]["lineage"] == "openai"


def test_rollout_uses_the_existing_format(monkeypatch, tmp_path):
    from xgen_rsi.base.host.rollouts import ROLLOUT_ENABLED_SETTING, rollout_directory

    client = ScriptedClient([text_step("done")])
    host = FakeHost(settings={ROLLOUT_ENABLED_SETTING: "1"}, storage_root=str(tmp_path))
    out = _go(monkeypatch, client, host, streaming=False)
    assert out == "done"
    files = list(rollout_directory(tmp_path).glob("rollout-*.jsonl"))
    assert len(files) == 1
    records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert records[0]["type"] == "pipeline.start"
    assert records[-1]["type"] == "pipeline.complete"
    assert {"type", "stage", "iteration", "timestamp", "data", "session_id", "run_id", "seq"} <= set(records[0])


def test_cli_provider_owns_the_loop_and_events_stream(monkeypatch):
    """CLI 공급자는 자기 루프를 돈다 — 커널은 호출 1회, 도구 사건은 CLI 스트림에서 번역한다."""
    from xgen_rsi.base.core.state import TokenUsage
    from xgen_rsi.base.llm_client.base import BaseClient, ClientCapabilities
    from xgen_rsi.base.llm_client.types import APIResponse, ContentBlock

    class _CLI(BaseClient):
        provider = "claude_code_cli"
        capabilities = ClientCapabilities(is_subprocess=True, supports_tools=True, streaming_granularity="message")

        async def _send(self, request: Any, *, purpose: str = "") -> APIResponse:  # pragma: no cover
            raise AssertionError("stream only")

        async def create_message_stream(self, **kwargs: Any):  # type: ignore[override]
            yield {"type": "tool_use", "id": "c1", "name": "mcp__connector__echo", "input": {"text": "z"}}
            yield {"type": "tool_result", "tool_use_id": "c1", "content": [{"type": "text", "text": "echo:z"}]}
            yield {"type": "text_delta", "text": "CLI 답"}
            yield {
                "type": "message_complete",
                "response": APIResponse(
                    content=[ContentBlock(type="text", text="CLI 답")],
                    stop_reason="end_turn",
                    usage=TokenUsage(input_tokens=5, output_tokens=2, cost_usd=0.01),
                    model="sonnet",
                ),
            }

    class _CLIHost(FakeHost):
        def build_cli_runtime(self, provider, params):
            return _CLI(api_key=""), None

    host = _CLIHost(tools=[EchoTool()])
    chunks = _go(monkeypatch, ScriptedClient([text_step("unused")]), host, provider="claude_code")
    events = [c["data"]["type"] for c in chunks if isinstance(c, dict) and c["type"] == "agent_event"]
    assert events == ["tool_call", "tool_result"]
    assert "CLI 답" in [c for c in chunks if isinstance(c, str)]
    usage = chunks[-1]["data"]
    assert usage["calls"] == 1 and usage["total_cost_usd"] == pytest.approx(0.01)


def test_guard_rejects_when_context_cannot_fit(monkeypatch):
    """요청이 창에 들어가지 않으면(압축 후에도) 명확한 오류로 끝난다 — 공급자 400 대신."""
    huge = [{"role": "user", "content": "z" * 200_000}]
    client = ScriptedClient([text_step("요약"), text_step("never")])
    host = FakeHost()
    chunks = _go(monkeypatch, client, host, memory=huge, context_window=2048, max_tokens=1024, enable_compaction=True)
    texts = "".join(c for c in chunks if isinstance(c, str))
    assert "[ERROR]" in texts


def test_skill_component_adds_catalog_and_read_tool(monkeypatch, tmp_path):
    """구조 레버(skill): 카탈로그는 프롬프트에, 전문은 ReadSkill 로 — 하네스 파일만으로 추가된다."""
    import dataclasses

    from xgen_rsi.harness.spec import ComponentSpec

    m = load_manifest(BUILTIN_H0)
    hdir = tmp_path / "h-skill"
    (hdir / "skills" / "csv-check").mkdir(parents=True)
    (hdir / "skills" / "csv-check" / "SKILL.md").write_text(
        "---\nname: csv-check\ndescription: Verify a CSV file against the requested columns before finishing.\n---\n"
        "1. Re-open the file.\n2. Compare the header with the request.\n3. Fix and re-check once.\n",
        encoding="utf-8",
    )
    comp = ComponentSpec(
        id="skill.library",
        kind="skill",
        impl="xgen_rsi.components.skills:SkillLibraryComponent",
        files=("skills/csv-check/SKILL.md",),
    )
    m = dataclasses.replace(m, components=m.components + (comp,), root=hdir)
    save_manifest(m, hdir)
    script = [tool_step("", [("t1", "ReadSkill", {"name": "csv-check"})]), text_step("확인 절차를 따랐습니다.")]
    client = ScriptedClient(script)
    host = FakeHost(settings={"XGEN_RSI_HARNESS_DIR": str(hdir)})
    chunks = _go(monkeypatch, client, host)
    system = client.requests[0]["system"]
    text = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False)
    assert "csv-check: Verify a CSV file" in text
    tools = [t["name"] for t in client.requests[0]["tools"]]
    assert "ReadSkill" in tools
    results = [c["data"] for c in chunks if isinstance(c, dict) and c["type"] == "agent_event" and c["data"]["type"] == "tool_result"]
    assert results and "Compare the header" in results[0]["result"]


def test_extra_blocks_accept_plain_strings_and_objects():
    """제안자는 extra_blocks 에 글을 그대로 넣는다(라운드 0 실측) — 글과 {"id","text"} 둘 다 조각이 된다."""
    from xgen_rsi.components.prompt import compose_parts

    out = compose_parts([("base", "B")], extra=["Check counts twice.", {"id": "x", "text": "Y"}, None, ""])
    assert out == [("base", "B"), ("extra", "\n\nCheck counts twice."), ("x", "\n\nY")]
