"""턴 정리(설계 41) — 다음 사용자 메시지의 정정이 신호가 되고, 재생으로 잰 RRSI 라운드가 하네스를 채택한다."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from tests.consolidate.test_world_replay import POLICY, _live
from tests.kernel.fakes import ScriptedClient, text_step
from xgen_rsi.consolidate import ConsolidationParams, ConsolidationState, Consolidator, TurnWorld
from xgen_rsi.consolidate.signals import checks_for, negative
from xgen_rsi.harness.payload import materialize
from xgen_rsi.harness.spec import load_manifest
from xgen_rsi.roles.llm import ScriptedRoleLLM

TABLE_BLOCK = "When the user asks to compare or summarize several items, present them as a Markdown table."


class TableAwareClient(ScriptedClient):
    """정책 대역 — 시스템 프롬프트에 표 지시가 있으면 표로, 없으면 산문으로 답한다."""

    def __init__(self) -> None:
        super().__init__([text_step("prose")])

    def _next(self) -> Dict[str, Any]:
        system = json.dumps(self.requests[-1]["system"], ensure_ascii=False)
        return text_step("| item | value |\n|---|---|\n| a | 1 |" if "Markdown table" in system else "Here is a long prose summary.")


def table_judge(criterion: str, *, reference: str = "", request: str = "", answer: str = ""):
    if "table" in criterion.lower():
        return ("|" in answer), "table check"
    return True, "other"


class _Roles:
    def __init__(self, proposer, critic, analyst):
        self.proposer, self.critic, self.analyst = proposer, critic, analyst


def _proposer(old: str, new: str) -> ScriptedRoleLLM:
    done = json.dumps({"action": "done", "summary": "present multi-item summaries as tables", "edits": [{
        "id": "e1", "component": "prompt", "hypothesis": "these users want tables for multi-item summaries",
        "targets_mode": "format", "predicted_affected": ["io-1"],
        "retroactive_check": "corrective: io-1 asked for a table; preservative: single facts unaffected; transfer: recurring preference"}]})
    return ScriptedRoleLLM([
        json.dumps({"action": "read_file", "path": "manifest.json"}),
        json.dumps({"action": "edit_file", "path": "manifest.json", "old": old, "new": new}),
        done,
        done,  # 검토자가 거절하면 수리 한 번(같은 편집을 다시 선언)
    ])


def _worlds(monkeypatch) -> List[TurnWorld]:
    _, _, r1 = _live(monkeypatch, [text_step("Here is a long prose summary.")], [], text="summarize the three options")
    _, _, r2 = _live(monkeypatch, [text_step("| item | value |")], [], text="no, show it as a table")
    return [TurnWorld(id="io-1", data=r1["world"], interaction_id="i-1", seq=1),
            TurnWorld(id="io-2", data=r2["world"], interaction_id="i-1", seq=2)]


def _consolidator(signal_kind: str = "correction", proposer=None, critic_verdict: str = "accept") -> Consolidator:
    signal = ScriptedRoleLLM([json.dumps({"kind": signal_kind, "criterion": "The answer presents the options as a table.",
                                          "evidence": "show it as a table"})])
    critic = ScriptedRoleLLM([json.dumps({"verdict": critic_verdict, "reasons": [] if critic_verdict == "accept" else ["leak"],
                                          "risk_notes": []})] * 4)
    analyst = ScriptedRoleLLM([json.dumps({"failure_modes": [{"mode": "prose instead of a table", "description": "x",
                                                              "worlds": ["io-1"]}], "success_habits": []})])
    proposer = proposer or _proposer('"extra_blocks": []', '"extra_blocks": [{"id": "table", "text": "' + TABLE_BLOCK + '"}]')
    return Consolidator(policy=POLICY, params=ConsolidationParams(m=1, k=2, parallel=2), client_factory=lambda plan: TableAwareClient(),
                        role_llms=_Roles(proposer, critic, analyst), judge=table_judge, signal_llm=signal)


def test_correction_in_the_next_message_leads_to_an_adopted_harness(monkeypatch):
    worlds = _worlds(monkeypatch)
    res = _consolidator().consolidate(ConsolidationState(), worlds, None, latest="io-2")
    assert res.signals["io-1"]["implicit"]["kind"] == "correction"
    assert res.status == "adopted", res.reason
    assert res.adopted and res.payload is not None and res.version != res.start_version
    hdir = materialize(res.payload)
    blocks = next(c for c in load_manifest(hdir).components if c.id == "prompt.system").params["extra_blocks"]
    assert blocks[0]["text"] == TABLE_BLOCK
    rnd = res.round
    assert rnd["t"] == 0 and rnd["winner"] == "A" and rnd["fresh"] == ["io-1"]
    assert rnd["incumbent"]["S"] == 0.0 and rnd["candidates"][0]["S"] == 1.0
    st = res.state
    assert st.t == 1 and st.progress == [0.0, 1.0]
    assert any(r.get("outcome") == "ACCEPTED" and r.get("component") == "prompt" for r in st.records)
    assert res.version in st.cache and "io-1" in st.cache[res.version], "the adopted harness's replays seed the next consolidation"
    json.dumps(st.to_json())  # 호스트가 그대로 저장한다


def test_next_consolidation_continues_the_agents_history(monkeypatch):
    worlds = _worlds(monkeypatch)
    first = _consolidator().consolidate(ConsolidationState(), worlds, None, latest="io-2")
    state = ConsolidationState.from_json(json.loads(json.dumps(first.state.to_json())))
    for w in worlds:
        if w.id in first.signals:
            w.signals = first.signals[w.id]
    # 같은 신호로 다시 — 새 신호가 없으니 라운드를 열지 않는다
    again = _consolidator().consolidate(state, worlds, first.payload, latest="io-2")
    assert again.status == "recorded" and again.state.t == 1


def test_no_signal_means_record_only(monkeypatch):
    worlds = _worlds(monkeypatch)
    res = _consolidator(signal_kind="neutral").consolidate(ConsolidationState(), worlds, None, latest="io-2")
    assert res.status == "recorded" and res.state.t == 0 and not res.adopted


def test_rejected_by_the_critic_keeps_the_harness_and_records_negative_evidence(monkeypatch):
    worlds = _worlds(monkeypatch)
    res = _consolidator(critic_verdict="reject").consolidate(ConsolidationState(), worlds, None, latest="io-2")
    assert res.status == "kept" and not res.adopted
    assert res.state.t == 1
    assert any(r.get("outcome") == "critic_reject" for r in res.state.records)


def test_current_harness_already_passing_is_kept_without_a_round(monkeypatch):
    worlds = _worlds(monkeypatch)
    first = _consolidator().consolidate(ConsolidationState(), worlds, None, latest="io-2")
    for w in worlds:
        if w.id in first.signals:
            w.signals = first.signals[w.id]
    res = _consolidator().consolidate(ConsolidationState(), worlds, first.payload, fresh=["io-1"])
    assert res.status == "kept" and res.state.t == 0, res.reason


def test_signals_to_checks():
    assert checks_for({}, answer="a") == []
    c = checks_for({"implicit": {"kind": "correction", "criterion": "Use a table."}}, answer="a")
    assert c == [{"kind": "answer_criteria", "name": "implicit_correction", "criterion": "Use a table."}]
    acc = checks_for({"implicit": {"kind": "accept"}}, answer="good answer")
    assert acc[0]["name"] == "implicit_accept" and acc[0]["reference"] == "good answer"
    assert negative({"stars": 1}) and negative({"implicit": {"kind": "complaint"}}) and not negative({"implicit": {"kind": "accept"}})


def test_missed_signals_are_read_by_a_later_consolidation(monkeypatch):
    """정리가 건너뛰어진 턴의 다음 메시지 신호도 나중 정리가 읽는다(latest 없이)."""
    worlds = _worlds(monkeypatch)
    res = _consolidator().consolidate(ConsolidationState(), worlds, None)
    assert res.signals["io-1"]["implicit"]["kind"] == "correction"
    assert res.status == "adopted", res.reason
