"""RSI 커널 고유 성질 — 원장(모든 정책 호출), 하네스 편집 반영, 계보, rollout, CLI 경로, 가드."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from tests.kernel.fakes import (
    EchoTool,
    FakeHost,
    ScriptedClient,
    overflow_step,
    text_step,
    tool_step,
)
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
    # 4.89 추정은 글자 부류로 센다: 같은 글자 반복(영문)은 적게, 한국어 문장은 제대로 센다.
    sentence = "한국어로 길게 쓴 지난 대화 문장입니다. "
    long_history = [
        {"role": "user", "content": "질문: " + sentence * 60},
        {"role": "assistant", "content": "답: " + sentence * 60},
    ] * 6
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
    """요청이 창에 들어가지 않으면(압축 후에도) 명확한 오류로 끝난다. 공급자 400 대신.

    지난 대화는 4.89 부터 요약으로 줄어들므로, 줄일 수 없는 것(지금 질문)이 창을 넘게 한다.
    """
    client = ScriptedClient([text_step("요약"), text_step("never")])
    host = FakeHost()
    chunks = _go(
        monkeypatch, client, host, text="질문 " * 20000, context_window=2048, max_tokens=1024,
        enable_compaction=True,
    )
    texts = "".join(c for c in chunks if isinstance(c, str))
    assert "[ERROR]" in texts
    assert client.requests == []


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


def test_failed_harness_load_reports_and_still_tears_down(monkeypatch, tmp_path):
    """하네스를 못 읽으면 기존 계약대로 [ERROR] 출력으로 끝나고, 조립이 만든 자원은 턴 정리로 닫는다."""
    client = ScriptedClient([text_step("ok")])
    host = FakeHost(settings={"XGEN_RSI_HARNESS_DIR": str(tmp_path / "missing")})
    chunks = _go(monkeypatch, client, host)
    text = "".join(c for c in chunks if isinstance(c, str))
    assert text.startswith("[ERROR] geny agent could not start:")
    assert client.requests == [] and host.finalized == 1


# ── 4.89 압축 정책: 넘침 복구, 호출 계수·기준점, 비율 범위, 고칠 수 없는 가드 ─────────────────────


def _korean_history(turns: int = 6, reps: int = 60) -> list:
    sentence = "한국어로 길게 쓴 지난 대화 문장입니다. "
    out = []
    for i in range(turns):
        out.append({"role": "user", "content": f"{i}번째 질문: " + sentence * reps})
        out.append({"role": "assistant", "content": f"{i}번째 답: " + sentence * reps})
    return out


@pytest.fixture
def captured_state(monkeypatch):
    from xgen_rsi.kernel.executor import RSITurnExecutor

    seen: list = []
    original = RSITurnExecutor.prepare

    def _prepare(self, plan, host):
        seen.append(plan.state)
        return original(self, plan, host)

    monkeypatch.setattr(RSITurnExecutor, "prepare", _prepare)
    return seen


def _events(state, kind: str) -> list:
    return [e.get("data") or {} for e in state.events if isinstance(e, dict) and e.get("type") == kind]


@pytest.mark.parametrize("streaming", [True, False])
def test_overflow_retries_once_after_compacting(monkeypatch, captured_state, streaming):
    """창 넘침 거절 → 강한 압축(요약 호출) → 같은 요청을 턴 기록에서 다시 지어 한 번 더. 사건은 하나씩."""
    client = ScriptedClient([overflow_step("prompt is too long"), text_step("요약입니다."), text_step("답입니다.")])
    out = _go(
        monkeypatch, client, FakeHost(), memory=_korean_history(), context_window=32000, streaming=streaming,
    )
    text = out if isinstance(out, str) else "".join(c for c in out if isinstance(c, str))
    assert text.endswith("답입니다.") and "[ERROR]" not in text
    assert ["compact" if "compact" in p else p for p in client.purposes] == ["main", "compact", "main"]
    # 다시 보낸 요청은 줄어든 이력으로 지었다.
    assert len(client.requests[2]["messages"]) < len(client.requests[0]["messages"])
    state = captured_state[-1]
    assert len(_events(state, "context.overflow")) == 1
    assert len(_events(state, "api.request")) == 1 and not _events(state, "api.error")
    assert [d.get("trigger") for d in _events(state, "context.compacted")] == ["overflow"]


@pytest.mark.parametrize("streaming", [True, False])
def test_second_overflow_propagates_with_one_error_event(monkeypatch, captured_state, streaming):
    """다시 보낸 요청도 거절되면 그대로 올라간다. ``api.error`` 는 한 번, 분류는 token_limit."""
    client = ScriptedClient(
        [overflow_step("prompt is too long"), text_step("요약입니다."), overflow_step("prompt is too long")]
    )
    out = _go(
        monkeypatch, client, FakeHost(), memory=_korean_history(), context_window=32000, streaming=streaming,
    )
    text = out if isinstance(out, str) else "".join(c for c in out if isinstance(c, str))
    assert "[ERROR] prompt is too long" in text
    assert len(client.requests) == 3
    state = captured_state[-1]
    errors = _events(state, "api.error")
    assert len(errors) == 1 and errors[0]["category"] == "token_limit"
    assert len(_events(state, "context.overflow")) == 1


def test_overflow_without_compaction_is_not_retried(monkeypatch, captured_state):
    """압축을 끈 에이전트는 넘침을 그대로 올린다(복구 훅이 False)."""
    client = ScriptedClient([overflow_step("prompt is too long"), text_step("never")])
    out = _go(monkeypatch, client, FakeHost(), memory=_korean_history(), enable_compaction=False, streaming=False)
    assert out.startswith("[ERROR]")
    assert len(client.requests) == 1
    assert not _events(captured_state[-1], "context.overflow")


async def test_usage_anchor_and_call_counter_follow_main_calls_only():
    """턴 기록으로 지은 호출만 압축 정책의 호출 계수를 올리고 공급자 입력 토큰을 기준점으로 남긴다."""
    from xgen_rsi.base.core.compaction import API_CALLS_KEY
    from xgen_rsi.base.core.config import ModelConfig
    from xgen_rsi.base.core.state import PipelineState
    from xgen_rsi.base.core.token_estimate import USAGE_ANCHOR_KEY
    from xgen_rsi.kernel.model_call import ModelCaller

    client = ScriptedClient([text_step("하나", usage=(1234, 5)), text_step("둘", usage=(99, 5))])
    caller = ModelCaller(client, stream=False)
    state = PipelineState()
    state.messages = [{"role": "user", "content": "안녕하세요"}]
    cfg = ModelConfig(model="fake-model", max_tokens=256)

    await caller.call(state, cfg, purpose="main")
    assert state.shared[API_CALLS_KEY] == 1
    anchor = state.shared[USAGE_ANCHOR_KEY]
    assert anchor

    await caller.call(state, cfg, purpose="verify", messages=[{"role": "user", "content": "다른 재료"}])
    assert state.shared[API_CALLS_KEY] == 1
    assert state.shared[USAGE_ANCHOR_KEY] == anchor


def _context_component(params: Dict[str, Any]):
    from xgen_rsi.components.context import StandardContextComponent
    from xgen_rsi.harness.spec import ComponentSpec

    return StandardContextComponent(
        ComponentSpec(
            id="context.standard",
            kind="context_mgmt",
            impl="xgen_rsi.components.context:StandardContextComponent",
            params=params,
        )
    )


@pytest.mark.parametrize(
    "params,expected",
    [
        ({}, (0.8, 0.5)),
        ({"proactive_ratio": 0.8, "target_ratio": 0.5}, (0.8, 0.5)),
        ({"proactive_ratio": 0.95, "target_ratio": 0.9}, (0.9, 0.8)),
        ({"proactive_ratio": 0.2, "target_ratio": 0.1}, (0.5, 0.2)),
        ({"proactive_ratio": 0.6, "target_ratio": 0.6}, (0.6, 0.5)),
        ({"proactive_ratio": 0.7, "target_ratio": 0.65}, (0.7, 0.6)),
        ({"proactive_ratio": "0.6", "target_ratio": None}, (0.8, 0.5)),
        ({"proactive_ratio": True, "target_ratio": False}, (0.8, 0.5)),
        ({"proactive_ratio": float("nan"), "target_ratio": float("nan")}, (0.8, 0.5)),
        ({"proactive_ratio": 1, "target_ratio": 0}, (0.9, 0.2)),
    ],
)
def test_evolvable_ratios_stay_in_bounds(params, expected):
    """제안자가 쓴 비율은 범위 안으로 묶인다(문턱 0.5~0.9, 목표 0.2~문턱-0.1). 숫자가 아니면 런타임 값."""
    assert _context_component(params).ratios() == expected


async def test_the_guard_cannot_be_turned_off():
    """옛 ``guard: false`` 는 더 읽지 않는다. 들어가지 않는 요청은 여전히 가드가 거절한다."""
    from types import SimpleNamespace

    from xgen_rsi.base.core.errors import GuardRejectError
    from xgen_rsi.base.core.state import PipelineState

    comp = _context_component({"guard": False, "guard_min_headroom": 0, "guard_extra": 0})
    state = PipelineState()
    state.context_window_budget = 2048
    state.max_tokens = 1024
    state.messages = [{"role": "user", "content": "질문 " * 5000}]
    events: list = []
    rt = SimpleNamespace(
        state=state,
        plan=SimpleNamespace(pipeline_kwargs={"enable_compaction": True}),
        emit=lambda kind, data: events.append((kind, data)),
    )
    with pytest.raises(GuardRejectError):
        await comp.ensure_fits(rt)
    assert [k for k, _ in events if k == "guard.check"]
    read = {key.split(".params.", 1)[1] for key in comp.params_read()}
    assert not read & {"guard", "guard_min_headroom", "guard_extra"}
    assert {"proactive_ratio", "target_ratio"} <= read


def test_memory_retriever_uses_the_store_hooks_capped_to_the_window():
    """검색기는 저장소에 붙은 hooks 를 쓰고, 주입 상한은 창의 10% 로 묶인다(runtime 4.88~4.89 build_pipeline)."""
    from types import SimpleNamespace

    from xgen_rsi.base.host.context_budget import memory_inject_cap_chars
    from xgen_rsi.base.memory.provider import MemoryHooks
    from xgen_rsi.components.memory import ArchiveMemoryComponent
    from xgen_rsi.harness.spec import ComponentSpec

    hooks = MemoryHooks(max_inject_chars=10_000, search_exclude_categories=("chatter",))
    store = SimpleNamespace(hooks=hooks)

    def retriever(window: int):
        comp = ArchiveMemoryComponent(
            ComponentSpec(id="memory.archive", kind="memory", impl="xgen_rsi.components.memory:ArchiveMemoryComponent")
        )
        rt = SimpleNamespace(memory_provider=store, plan=SimpleNamespace(pipeline_kwargs={"context_window_budget": window}))
        return comp.retriever(rt)

    small = retriever(16_000)._hooks
    assert small.max_inject_chars == memory_inject_cap_chars(16_000, 10_000) < 10_000
    assert small.search_exclude_categories == ("chatter",)
    assert hooks.max_inject_chars == 10_000  # 저장소의 hooks 는 그대로
    assert retriever(0)._hooks is hooks


@pytest.mark.parametrize("override,expected", [(None, False), (True, True)])
def test_tool_context_knows_whether_the_model_sees_images(monkeypatch, override, expected):
    """Read 가 그림을 건넬지 정하는 표시(4.86 ``MODEL_VISION_KEY``)가 이 턴의 도구 컨텍스트에 실린다."""
    from xgen_rsi.base.tools.built_in.read_tool import MODEL_VISION_KEY
    from xgen_rsi.kernel.executor import RSITurnExecutor

    plans: list = []
    original = RSITurnExecutor.prepare

    def _prepare(self, plan, host):
        plans.append(plan)
        return original(self, plan, host)

    monkeypatch.setattr(RSITurnExecutor, "prepare", _prepare)
    extra = {} if override is None else {"supports_vision": override}
    _go(monkeypatch, ScriptedClient([text_step("ok")]), FakeHost(tools=[EchoTool()]), streaming=False, **extra)
    assert plans[-1].run_tool_context.metadata[MODEL_VISION_KEY] is expected
