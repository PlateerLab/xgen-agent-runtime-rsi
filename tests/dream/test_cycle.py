"""DreamCycle end to end with a scripted policy-development LLM."""

from __future__ import annotations

import json

import pytest

from tests.explore.synthetic import synth_world
from xgen_rsi.dream.cycle import ConfirmationResult, CycleConfig, DreamCycle
from xgen_rsi.dream.develop import (
    POLICY_DEV_PROMPT,
    PolicyDeveloper,
    VersionRecord,
    extract_policy_source,
)
from xgen_rsi.dream.evaluator import EvalConfig
from xgen_rsi.dream.manifest import load_live_manifests, read_live_manifest, write_live_manifest
from xgen_rsi.dream.world import WorldPool
from xgen_rsi.explore.api import LiveCycleManifest
from xgen_rsi.explore.policies import builtin_policy_source
from xgen_rsi.roles.llm import ScriptedRoleLLM

PI0 = builtin_policy_source("parallel_refine")
GOOD = (builtin_policy_source("portfolio")
        .replace("class PortfolioPolicy(LLMDesignedMethod):", "class OptimalPolicy(LLMDesignedMethod):")
        .replace("OptimalPolicy = PortfolioPolicy\n", "")).strip() + "\n"
BAD = PI0.replace("from __future__ import annotations", "from __future__ import annotations\nimport os")
WEAK = '''"""Weak policy: probes one root and stops."""
from xgen_rsi.explore.api import LLMDesignedMethod, SimResult, bootstrap_plan

NAME = "OptimalPolicy"


class OptimalPolicy(LLMDesignedMethod):
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", 0.3))

    def solve(self, question, budget=None):
        question.reset()
        roots = question.legal_roots()
        if roots:
            question.probe_batch(roots[:1])
        return SimResult()

    def plan_grid(self, context):
        return bootstrap_plan(context, "weak: bootstrap")
'''


def fenced(src: str) -> str:
    return f"Here is the policy.\n```python\n{src}```\n"


def _pool() -> WorldPool:
    return WorldPool(synth_world(s) for s in range(10))


def _cycle(tmp_path, llm, *, confirm=None, mode="xgen", M=3, name="cycle", live=()):
    cfg = CycleConfig(M=M, mode=mode, eval=EvalConfig(mode=mode, W=4))
    return DreamCycle(tmp_path / name, _pool(), PI0, llm=llm, iteration=2, config=cfg,
                      online_confirm=confirm, live_manifests=live)


def test_full_cycle_promotes_after_confirmation(tmp_path) -> None:
    llm = ScriptedRoleLLM([fenced(BAD), fenced(GOOD), fenced(WEAK)])
    seen = []

    def confirm(req):
        seen.append(req)
        return ConfirmationResult(True, "floor and cost rule passed", {"delta_S": 0.01})

    res = _cycle(tmp_path, llm, confirm=confirm).run()
    assert res.labels == ("r0000_pi0", "r0001_dev", "r0002_dev")
    assert res.eligible == (True, True, True)
    assert res.m_star == 1 and res.status == "promoted" and res.promoted_index == 1
    assert res.V_selection[1] >= res.V_selection[0] > res.V_selection[2]
    assert res.promoted_source == GOOD
    assert len(seen) == 1 and seen[0].candidate_index == 1 and seen[0].incumbent_V == res.V_selection[0]
    assert len(llm.calls) == 3                    # one repair for BAD, then GOOD, then WEAK
    d = tmp_path / "cycle"
    # the repair prompt carries the validation report
    repair = (d / "history/r0001_dev/develop/attempt_1_prompt.txt").read_text()
    assert "rejected" in repair and "import" in repair
    first = json.loads((d / "history/r0001_dev/develop/attempt_0_report.json").read_text())
    assert not first["ok"]
    for v in ("r0000_pi0", "r0001_dev", "r0002_dev", "baseline"):
        assert (d / "history" / v / "proposal_results" / "beta_sweep.json").exists()
        assert (d / "history" / v / "proposal_results" / "policy_execution_traces.jsonl").exists()
    manifest = json.loads((d / "cycle_manifest.json").read_text())
    assert manifest["complete"] and manifest["status"] == "promoted"
    assert not manifest["shared_worlds"] and manifest["selection_worlds"]
    assert set(manifest["dev_worlds"]).isdisjoint(manifest["selection_worlds"])
    assert (d / "promoted_policy.py").read_text() == GOOD
    # β reference: insufficient live history → 0.6 default; developed versions checked against it
    assert res.beta_decision["branch"] == "default" and res.beta_decision["beta"] == 0.6
    status = json.loads((d / "history/r0002_dev/status.json").read_text())
    assert status["beta_check"]["baked"] == 0.3 and not status["beta_check"]["within_step"]
    # the next live cycle sidecar carries the promoted policy's baked β and grid plan
    cur = read_live_manifest(d / "_current" / "live_cycle_manifest.json")
    assert cur.iteration == 3 and cur.beta == 0.6 and cur.best_score is None
    assert cur.policy_version == "r0001_dev" and cur.planned_branch_count >= 1
    assert res.next_plan and res.next_beta == 0.6
    assert res.pi0_sweep.iteration == 2 and len(res.pi0_sweep.points) == 6
    # the development prompt: rules restated, history inlined, β hint
    prompt = llm.calls[0]["prompt"]
    assert "pareto.reward" in prompt and "_schedule(beta)" in prompt and "plan_grid" in prompt
    assert "history/r0000_pi0/policy.py" in prompt and "history/baseline/policy.py" in prompt
    assert "beta = 0.6 (default" in prompt
    later = llm.calls[2]["prompt"]
    assert "history/r0001_dev/policy.py" in later and "policy_execution_traces.jsonl" in later


def test_online_veto_keeps_pi0(tmp_path) -> None:
    llm = ScriptedRoleLLM([fenced(GOOD), fenced(WEAK)])
    res = _cycle(tmp_path, llm, confirm=lambda req: False).run()
    assert res.m_star == 1 and res.status == "vetoed" and res.promoted_index == 0
    assert res.promoted_source == PI0
    assert res.confirmation == {"approved": False, "reason": "", "details": {}, "candidate_index": 1}
    cur = read_live_manifest(tmp_path / "cycle" / "_current" / "live_cycle_manifest.json")
    assert cur.policy_version == "r0000_pi0"


def test_without_hook_the_winner_waits(tmp_path) -> None:
    res = _cycle(tmp_path, ScriptedRoleLLM([fenced(GOOD), fenced(WEAK)])).run()
    assert res.m_star == 1 and res.status == "pending_confirmation" and res.promoted_index == 0


def test_cycle_resumes_without_repeating_llm_or_hook(tmp_path) -> None:
    calls = {"n": 0}

    def crashing(req):
        calls["n"] += 1
        raise RuntimeError("operator went home")

    with pytest.raises(RuntimeError, match="operator"):
        _cycle(tmp_path, ScriptedRoleLLM([fenced(GOOD), fenced(WEAK)]), confirm=crashing).run()
    # every version is archived; the rerun must not call the (now empty) LLM again
    done = _cycle(tmp_path, ScriptedRoleLLM([]), confirm=lambda req: {"approved": True,
                                                                      "reason": "ok"}).run()
    assert done.status == "promoted" and done.m_star == 1 and calls["n"] == 1
    again = _cycle(tmp_path, ScriptedRoleLLM([]), confirm=crashing).run()
    assert again.to_json() == done.to_json() and calls["n"] == 1


def test_failed_development_and_no_llm(tmp_path) -> None:
    res = _cycle(tmp_path, ScriptedRoleLLM([fenced(BAD)] * 3 + [fenced(BAD)] * 3),
                 name="bad").run()
    assert res.eligible == (True, False, False) and res.m_star == 0 and res.status == "kept"
    status = json.loads((tmp_path / "bad/history/r0001_dev/status.json").read_text())
    assert status["state"] == "rejected" and "import" in status["reason"]
    none = _cycle(tmp_path, None, name="nollm", M=2).run()
    assert none.status == "kept" and none.V_selection[1] is None


def test_paper_mode_shares_worlds_and_promotes_directly(tmp_path) -> None:
    res = _cycle(tmp_path, ScriptedRoleLLM([fenced(GOOD)]), mode="paper", M=2).run()
    manifest = json.loads((tmp_path / "cycle" / "cycle_manifest.json").read_text())
    assert manifest["shared_worlds"] and manifest["dev_worlds"] == manifest["selection_worlds"]
    assert res.V_selection[res.m_star] >= res.V_selection[0]
    assert res.status in ("promoted", "kept") and res.confirmation is None
    assert res.V_selection == res.V_dev


def test_live_history_feeds_the_beta_rule(tmp_path) -> None:
    pool = tmp_path / "trace_pool"
    for it, best in ((0, 0.50), (1, 0.60), (2, 0.61)):
        write_live_manifest(pool / f"iter{it:04d}", LiveCycleManifest(
            iteration=it, beta=0.6, best_score=best, effective_branch_count=8,
            effective_refine_count=5, opened_width=8, probes=30))
    write_live_manifest(pool / "_current", LiveCycleManifest(iteration=3, beta=0.6, best_score=None))
    live = load_live_manifests(pool, include_current=True)
    assert [m.iteration for m in live] == [0, 1, 2]
    res = _cycle(tmp_path, ScriptedRoleLLM([fenced(WEAK)]), M=2, live=live).run()
    assert res.beta_decision["branch"] in ("keep", "raise", "lower", "default")
    assert "live best" in res.beta_decision["reason"]


def test_prompt_helpers() -> None:
    assert extract_policy_source("```python\nx = 1\n```\n```py\ny = 22222\n```") == "y = 22222\n"
    assert extract_policy_source("raw = 1") == "raw = 1\n"
    for key in ("{method_file}", "{history_dir}", "{beta_hint}", "{history}"):
        assert key in POLICY_DEV_PROMPT
    dev = PolicyDeveloper(ScriptedRoleLLM([]), max_repairs=0)
    out = dev.develop([VersionRecord(0, "r0000_pi0", PI0, origin="incumbent")])
    assert not out.ok and "llm error" in out.attempts[0]["report"]["problems"][0]
