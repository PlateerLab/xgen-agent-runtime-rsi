"""에이전트마다 자기 하네스(설계 40) — 사용 기록 → 과제 → 그 에이전트의 RRSI 진화 → 채택 하네스 → 다음 턴.

실제 모델은 부르지 않는다. 정책은 하네스가 만든 시스템 프롬프트에 따라 답하는 각본 클라이언트, 역할은 각본 역할 LLM, 판정은 각본 판정이다.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from tests.evolve.support import GOOD, accept_verdicts, block_edit, edit_decl, j, make_roles
from tests.kernel.fakes import FakeHost, ScriptedClient, text_step
from xgen_rsi.agent_evolution import AgentEvolution
from xgen_rsi.base.host import runner as runner_mod
from xgen_rsi.evolve.judge import CriteriaJudge
from xgen_rsi.evolve.runner import PolicySpec
from xgen_rsi.evolve.verifiers import verify
from xgen_rsi.harness.payload import materialize, payload_version, to_payload
from xgen_rsi.harness.spec import HarnessSpecError, load_manifest, save_manifest
from xgen_rsi.kernel.executor import BUILTIN_H0
from xgen_rsi.roles.llm import RoleModel, ScriptedRoleLLM
from xgen_rsi.turn_executor import GenyRSITurnExecutor
from xgen_rsi.usage import (
    AgentSettings,
    NotEnoughUsage,
    UsageItem,
    build_suite,
    criteria_for,
    render_prompt,
    split_of,
    task_from_usage,
    task_id_for,
)

# ── 사용 기록 → 과제 ─────────────────────────────────────────────────────────


def test_feedback_and_expected_answers_become_criteria():
    expected = criteria_for(UsageItem(id="1", request="q", expected="42"))
    assert expected == [{"kind": "answer_criteria", "name": "expected", "criterion": expected[0]["criterion"], "reference": "42"}]
    bad = criteria_for(UsageItem(id="2", request="q", answer="a", stars=1, issue="환각(허위 정보)", comment="없는 수치를 지어냈다"))
    assert len(bad) == 1 and "환각(허위 정보)" in bad[0]["criterion"] and "없는 수치를 지어냈다" in bad[0]["criterion"]
    low = criteria_for(UsageItem(id="3", request="q", answer="a", stars=2))
    assert "rated this answer as poor" in low[0]["criterion"]
    good = criteria_for(UsageItem(id="4", request="q", answer="the accepted answer", stars=5, issue="이슈없음"))
    assert good[0]["name"] == "accepted" and good[0]["reference"] == "the accepted answer"
    mine = criteria_for(UsageItem(id="5", request="q", criteria=["표로 답한다", "  "]))
    assert [c["criterion"] for c in mine] == ["표로 답한다"]
    # 신호가 없는 턴은 과제가 아니다
    assert criteria_for(UsageItem(id="6", request="q", answer="a", stars=3)) == []
    assert task_from_usage(UsageItem(id="6", request="q", answer="a")) is None
    assert task_from_usage(UsageItem(id="7", request="  ", expected="x")) is None


def test_tasks_carry_the_agent_settings_and_the_conversation():
    item = UsageItem(id="io-9", request="이번 달 합계는?", expected="120",
                     history=[{"role": "user", "content": "1월 장부를 열어 줘"}, {"role": "assistant", "content": "열었습니다"}])
    task = task_from_usage(item, AgentSettings(system_prompt="장부 비서", max_iterations=12))
    assert task.id == task_id_for(item) and task.system_prompt == "장부 비서" and task.max_iterations == 12
    assert task.prompt == render_prompt(item)
    assert "User: 1월 장부를 열어 줘" in task.prompt and task.prompt.endswith("Current request:\n이번 달 합계는?")
    assert task.toolset == "workspace" and "usage" in task.tags


def test_build_suite_splits_deterministically_and_needs_enough_usage(tmp_path):
    items = [UsageItem(id=f"io-{i}", request=f"q{i}", expected="x") for i in range(20)]
    suite = build_suite(items + [UsageItem(id="no-signal", request="q", answer="a")], tmp_path / "s", min_evolve=4)
    assert suite.skipped == 1 and len(suite.tasks) == 20
    assert set(suite.splits["evolve"]).isdisjoint(suite.splits["heldout"])
    assert all(split_of(t) == "evolve" for t in suite.splits["evolve"]) and suite.splits["heldout"]
    assert json.loads((tmp_path / "s" / "suite.json").read_text())["splits"]["evolve"] == list(suite.splits["evolve"])
    with pytest.raises(NotEnoughUsage):
        build_suite(items[:2], tmp_path / "t", min_evolve=4)


# ── 기준 판정 ─────────────────────────────────────────────────────────────────


def test_criteria_checks_are_graded_by_the_judge_outside_the_harness(tmp_path):
    seen = []

    def judge(criterion, *, reference, request, answer):
        seen.append((criterion, reference, request, answer))
        return ("120" in answer), "has the total"

    checks = [{"kind": "answer_criteria", "criterion": "same as reference", "reference": "120"}]
    assert verify(checks, workspace=str(tmp_path), answer="합계는 120 입니다", judge=judge, request="합계는?").reward == 1.0
    assert seen == [("same as reference", "120", "합계는?", "합계는 120 입니다")]
    assert verify(checks, workspace=str(tmp_path), answer="모르겠습니다", judge=judge).reward == 0.0
    no_judge = verify(checks, workspace=str(tmp_path), answer="120")
    assert no_judge.reward == 0.0 and no_judge.checks[0].detail == "no judge configured"
    assert len(seen) == 2  # 빈 판정 모델에는 묻지 않는다


def test_criteria_judge_asks_once_per_answer_and_never_passes_on_errors():
    llm = ScriptedRoleLLM([j({"pass": True, "reason": "same total"})], role="judge")
    judge = CriteriaJudge(RoleModel(provider="openai", model="m"), llm=llm)
    assert judge("c", reference="r", request="q", answer="a") == (True, "same total")
    assert judge("c", reference="r", request="q", answer="a") == (True, "same total")  # 캐시 — 두 번째 응답을 쓰지 않는다
    broken = CriteriaJudge(RoleModel(provider="openai", model="m"), llm=ScriptedRoleLLM(["not json"], role="judge"))
    ok, reason = broken("c", request="q", answer="b")
    assert ok is False and reason


# ── 하네스 페이로드 ────────────────────────────────────────────────────────────


def _payload_with_block(tmp_path, text: str) -> Dict[str, Any]:
    m = load_manifest(BUILTIN_H0).with_param("prompt.system.params.extra_blocks", [{"id": "agent", "text": text}])
    save_manifest(m, tmp_path / "h")
    return to_payload(tmp_path / "h")


def test_payload_round_trip_and_tamper_check(tmp_path):
    payload = to_payload(BUILTIN_H0)
    assert payload["version"] == load_manifest(BUILTIN_H0).version_id() == payload_version(payload)
    root = materialize(payload, tmp_path / "cache")
    assert load_manifest(root).version_id() == payload["version"]
    assert materialize(payload, tmp_path / "cache") == root  # 같은 버전은 다시 풀지 않는다
    bad = dict(payload, version="sha256:" + "0" * 64)
    with pytest.raises(HarnessSpecError):
        materialize(bad, tmp_path / "cache")
    with pytest.raises(HarnessSpecError):
        materialize({"manifest": payload["manifest"], "files": {"../escape.md": "x"}}, tmp_path / "cache")


# ── 턴: 에이전트 하네스 훅 · 궤적 훅 ───────────────────────────────────────────


class _AgentHost(FakeHost):
    def __init__(self, payload=None, **kw):
        super().__init__(**kw)
        self._payload = payload
        self.records = []

    def rsi_agent_harness(self):
        return self._payload

    def rsi_record(self, record):
        self.records.append(record)


def _turn(monkeypatch, host, client):
    monkeypatch.setattr(runner_mod, "build_client", lambda *a, **k: client)
    out = GenyRSITurnExecutor().run(host, text="hi", provider="openai", workflow_id="wf", user_id="u",
                                    interaction_id="i-1", enable_memory=False, memory_distill=False, streaming=False)
    return out


def test_turn_runs_the_agents_own_harness_and_reports_the_trajectory(monkeypatch, tmp_path):
    payload = _payload_with_block(tmp_path, "AGENT-OWN-HARNESS")
    client = ScriptedClient([text_step("ok")])
    host = _AgentHost(payload, settings={"XGEN_RSI_HARNESS_CACHE": str(tmp_path / "cache")})
    assert _turn(monkeypatch, host, client) == "ok"
    system = client.requests[0]["system"]
    assert "AGENT-OWN-HARNESS" in (system if isinstance(system, str) else json.dumps(system, ensure_ascii=False))
    assert len(host.records) == 1
    rec = host.records[0]
    assert rec["lineage"] == "agent" and rec["harness_id"] == payload["version"] and rec["status"] == "completed"
    assert rec["final_text"] is None and rec["transcript"] is None  # 운영 기록은 구조·비용만


def test_no_agent_harness_means_h0_and_the_admin_pin_still_wins(monkeypatch, tmp_path):
    client = ScriptedClient([text_step("ok"), text_step("ok")])
    host = _AgentHost(None)
    _turn(monkeypatch, host, client)
    assert host.records[0]["lineage"] == "builtin:h0"
    pinned = _AgentHost(_payload_with_block(tmp_path, "NOT-USED"), settings={"XGEN_RSI_HARNESS_DIR": "builtin:h0"})
    _turn(monkeypatch, pinned, client)
    assert pinned.records[0]["lineage"] == "builtin:h0"


# ── 에이전트 진화: 사용 → 과제 → RRSI → 채택 → 다음 턴 ──────────────────────────


class _AnswerByHarness(ScriptedClient):
    """하네스가 만든 시스템 프롬프트에 GOOD 이 있으면 'DONE', 없으면 'PENDING' 으로 답한다."""

    def _next(self) -> Dict[str, Any]:
        system = str(self.requests[0].get("system")) if self.requests else ""
        return text_step("STATUS: DONE" if GOOD in system else "STATUS: PENDING")


def _judge(criterion, *, reference, request, answer):
    return ("DONE" in answer), "status line"


def _usage(n: int = 16):
    return [UsageItem(id=f"io-{i}", request=f"요청 {i}: 상태를 알려 줘", answer="STATUS: PENDING", stars=1,
                      issue="응답 실패", comment="상태가 DONE 이어야 한다") for i in range(n)]


def _scripts(task_id: str):
    proposer = [j({"action": "list_components"}),
                j({"action": "read_trace", "task_id": task_id, "from_step": 1, "to_step": 2}),
                j(block_edit(GOOD)),
                j({"action": "done", "summary": "confirm the status line", "edits": [edit_decl(predicted_affected=[task_id])]})]
    analyst = [j({"action": "digest_many", "requests": [{"task_id": task_id, "lens": "failure"}]}),
               j({"action": "report", "failure_modes": [{"mode": "unverified_status", "n_tasks": 3, "affected_tasks": [task_id],
                                                         "description": "The status is reported without checking.",
                                                         "needed_instead": "A bounded check."}],
                  "capability_gaps": [], "success_habits": []})]
    digester = [j({"action": "grep", "pattern": "VERIFIER|FAIL", "path": f"{task_id}.txt"}),
                j({"action": "return", "digest": {"lens": "failure", "blocker": "status not confirmed", "narrative": "Answered once.",
                                                  "evidence": [{"where": "step 1", "quote": "PENDING"}],
                                                  "verifier_evidence": "criterion failed", "needed_instead": "confirm"}})]
    return make_roles(proposer=proposer, critic=accept_verdicts(), analyst=analyst, digester=digester)


_KNOBS = dict(m=1, b_min=1, b_max=2, w=3, m_draft=1, delta=0.05, repair_rounds=2, n_fail_traces=3, n_success_traces=1,
              trial_parallel=1, digest_parallel=1, smoke_n=1, mode="xgen", early_stop=False)


def test_agent_evolution_adopts_a_harness_from_usage_and_the_next_turn_uses_it(monkeypatch, tmp_path):
    usage = _usage()
    suite_probe = build_suite(usage, tmp_path / "probe")
    first = suite_probe.splits["evolve"][0]
    evo = AgentEvolution(tmp_path / "evo", policy=PolicySpec(provider="openai", model="fake-model"), roles={},
                         agent=AgentSettings(system_prompt="상태 비서"), T=1, k=1, overrides=_KNOBS,
                         client_factory=lambda plan: _AnswerByHarness([text_step("unused")]),
                         role_llms=_scripts(first), judge=_judge)
    result = evo.run(usage)
    assert result.status == "done" and result.adopted
    assert result.baseline["S"] == pytest.approx(0.0) and result.final["S"] == pytest.approx(1.0)
    assert result.start_version == load_manifest(BUILTIN_H0).version_id() != result.version
    assert result.rounds[0].winner == "A" and result.rounds[0].candidates[0]["outcome"] == "ACCEPTED"
    assert result.tasks["evolve"] == len(suite_probe.splits["evolve"]) and result.tasks["skipped"] == 0
    assert result.heldout["start"]["S"] == pytest.approx(0.0) and result.heldout["adopted"]["S"] == pytest.approx(1.0)
    assert GOOD in json.dumps(result.payload["manifest"])
    assert "api_key" not in json.dumps(result.to_json())

    # 채택된 하네스를 그 에이전트의 다음 턴에 준다 — 답이 바뀐다
    client = _AnswerByHarness([text_step("unused")])
    host = _AgentHost(result.payload, settings={"XGEN_RSI_HARNESS_CACHE": str(tmp_path / "cache")})
    assert _turn(monkeypatch, host, client) == "STATUS: DONE"
    assert host.records[0]["harness_id"] == result.version

    # 진화는 그 에이전트의 현재 하네스에서 이어서 시작한다
    again = AgentEvolution(tmp_path / "evo2", policy=PolicySpec(provider="openai", model="fake-model"), roles={},
                           T=0, k=1, overrides=_KNOBS, client_factory=lambda plan: _AnswerByHarness([text_step("unused")]),
                           role_llms=make_roles(), judge=_judge)
    cont = again.run(usage, current=result.payload)
    assert cont.start_version == result.version and cont.baseline["S"] == pytest.approx(1.0) and not cont.adopted


def test_agent_evolution_reports_when_usage_is_not_enough(tmp_path):
    evo = AgentEvolution(tmp_path / "evo", policy=PolicySpec(provider="openai", model="m"), roles={},
                         role_llms=make_roles(), judge=_judge)
    res = evo.run([UsageItem(id="1", request="q", answer="a")])
    assert res.status == "not_enough_usage" and not res.adopted and "need at least" in res.message


def test_agent_evolution_requires_a_judge_and_the_four_roles():
    with pytest.raises(ValueError, match="judge"):
        AgentEvolution("x", policy=PolicySpec(provider="openai", model="m"),
                       roles={r: {"provider": "openai", "model": "m"} for r in ("proposer", "critic", "analyst", "digester")})
