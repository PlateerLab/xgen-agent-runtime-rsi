"""Evaluator: Eq.1 at the baked β, β sweep, archive files (beta_sweep.json, traces)."""

from __future__ import annotations

import json

import pytest

from tests.dream.test_hiding import _policy
from tests.explore.synthetic import synth_worlds, toy_world
from xgen_rsi.dream.evaluator import (
    DEFAULT_BETA_GRID,
    EvalConfig,
    evaluate_policy,
    load_sweep,
    sweep_points_from_json,
)
from xgen_rsi.dream.sandbox import load_policy
from xgen_rsi.explore.api import GridPlan, LLMDesignedMethod, SimResult
from xgen_rsi.explore.policies import PortfolioPolicy, builtin_policy_source
from xgen_rsi.rsi_math import BetaSweep


def test_defaults_follow_the_decision_table() -> None:
    cfg = EvalConfig()
    assert cfg.beta_grid == DEFAULT_BETA_GRID == (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    assert cfg.lam == 0.1 and cfg.root_policy == "choose" and cfg.root_multi and cfg.apply_plan
    assert cfg.betas_eq1(4) == (pytest.approx(0.25 / 110), pytest.approx(0.05 / 4))
    paper = EvalConfig(mode="paper")
    assert paper.root_policy == "earliest" and not paper.root_multi and not paper.apply_plan
    assert EvalConfig(default_W=3).W_for(toy_world()) == 3
    with pytest.raises(ValueError):
        EvalConfig(beta_grid=(1.5,))


def test_archive_files(tmp_path) -> None:
    worlds = synth_worlds(3)
    loaded = load_policy(builtin_policy_source("portfolio"))
    ev = evaluate_policy(loaded, worlds, EvalConfig(W=3), label="r0000_pi0", out_dir=tmp_path)
    assert ev.eligible and ev.baked_beta == 0.6
    sweep = json.loads((tmp_path / "proposal_results" / "beta_sweep.json").read_text())
    assert sweep == load_sweep(tmp_path)
    assert sweep["pareto_auc_version"] == "v1" and sweep["lambda"] == 0.1
    assert sweep["pareto.reward"] == pytest.approx(sweep["pareto.auc"] - 0.1 * sweep["parallel_penalty"])
    assert [p["beta"] for p in sweep["frontier"]] == list(DEFAULT_BETA_GRID)
    assert set(sweep["per_world"]) == {w.world_id for w in worlds}
    for p in sweep["frontier"]:
        assert 0.0 <= p["work"] <= 1.0 and 0.0 <= p["attainment"] <= 1.0
    pts = sweep_points_from_json(sweep)
    assert BetaSweep(iteration=1, points=pts).points[0].beta == 0.0
    assert ev.sweep_points() == pts
    lines = (tmp_path / "proposal_results" / "policy_execution_traces.jsonl").read_text().splitlines()
    assert len(lines) == len(worlds) * (1 + len(DEFAULT_BETA_GRID))
    rec = json.loads(lines[-1])
    assert rec["role"] == "sweep" and rec["beta"] == 1.0 and rec["decision_rounds"]
    rnd = rec["decision_rounds"][0]
    assert {"prefix", "batch", "revealed"} <= set(rnd)
    summary = json.loads((tmp_path / "eval.json").read_text())
    assert summary["V"] == pytest.approx(ev.V) and summary["label"] == "r0000_pi0"
    # Eq.1 decomposition: V_i = best − cost + parallel bonus
    for wid, t in ev.eq1.items():
        assert ev.V_by_world[wid] == pytest.approx(t["best"] - t["cost"] + t["parallel_bonus"])


class OutOfSupport(PortfolioPolicy):
    def plan_grid(self, context):
        return GridPlan(context.trace_branch_count + 1, 0, "one branch too many")


def test_out_of_support_earns_no_replay_reward() -> None:
    ev = evaluate_policy(OutOfSupport, [toy_world()], EvalConfig(W=2))
    assert ev.V == 0.0 and ev.eq1["toy"]["stop_reason"] == "out_of_support"
    assert ev.pareto_auc == 0.0
    off = evaluate_policy(OutOfSupport, [toy_world()], EvalConfig(W=2, apply_plan=False))
    assert off.V > 0.5


class Crashy(LLMDesignedMethod):
    def solve(self, question, budget=None):
        if self.beta > 0.9:
            raise RuntimeError("boom")
        return SimResult()


def test_errors_make_a_version_ineligible() -> None:
    ev = evaluate_policy(Crashy, [toy_world()], EvalConfig(W=2, apply_plan=False))
    assert not ev.eligible and any("boom" in e for e in ev.errors)
    slow = load_policy(_policy("while True:\n    pass"), timeout=0.2)
    ev2 = evaluate_policy(slow, [toy_world()], EvalConfig(W=2, beta_grid=(0.5,)), sweep=True)
    assert not ev2.eligible and all("timeout" in e for e in ev2.errors)
