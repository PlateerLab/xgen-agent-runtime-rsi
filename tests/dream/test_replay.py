"""Replay transition: Child semantics, the three stop conditions, out-of-support,
determinism, and the 05 §8.8 toy world numbers."""

from __future__ import annotations

import pytest

from tests.explore.synthetic import TOY_ROOT, synth_world, toy_world
from xgen_rsi.dream.evaluator import EvalConfig, evaluate_policy
from xgen_rsi.dream.replay import ReplayQuestion, replay_state, run_episode
from xgen_rsi.dream.sandbox import load_policy
from xgen_rsi.dream.world import World, WorldCell
from xgen_rsi.explore.api import ROOT, GridPlan, LLMDesignedMethod, Observation, SimResult
from xgen_rsi.explore.policies import builtin_policy_source
from xgen_rsi.rsi_math import (
    episode_penalty,
    pareto_auc_v1,
    pareto_reward,
    replay_value,
    select_policy,
)


class Scripted(LLMDesignedMethod):
    """Probes a fixed batch sequence (test helper, trusted code)."""

    BATCHES: list[list[str]] = []

    def solve(self, question, budget=None):
        question.reset()
        for batch in self.BATCHES:
            if not question.legal_actions():
                break
            question.probe_batch(batch)
        return SimResult()


def scripted(*batches: list[str]) -> type:
    return type("S", (Scripted,), {"BATCHES": [list(b) for b in batches]})


SERIAL_A = scripted(["b0a0"], ["b0a1"], ["b0a2"])
PARALLEL_B = scripted(["b0a0", "b1a0"], ["b0a1", "b1a1"])


def test_toy_world_reproduces_05_8_8() -> None:
    w = toy_world()
    a = run_episode(SERIAL_A, w, None, 2)
    b = run_episode(PARALLEL_B, w, None, 2)
    raw = w.normalizer("paper")
    V_A = replay_value(a.episode, beta_cost=0.01, beta_par=0.02, norm=raw)
    V_B = replay_value(b.episode, beta_cost=0.01, beta_par=0.02, norm=raw)
    assert V_A == pytest.approx(0.61, abs=1e-12) and V_B == pytest.approx(0.70, abs=1e-12)
    assert (a.episode.n_revealed, a.episode.rounds, b.episode.n_revealed, b.episode.rounds) == (3, 3, 4, 2)
    assert episode_penalty(a.episode) == 1.0 and episode_penalty(b.episode) == 0.5
    assert a.episode.stop_reason == b.episode.stop_reason == "empty_batch"
    norm = w.normalizer()
    pts = [(e.n_revealed / e.N_max, max(norm(s) for s in (*e.revealed_scores, TOY_ROOT)))
           for e in (a.episode, b.episode)]
    assert pts[0] == (0.5, pytest.approx(0.7333, abs=1e-4)) and pts[1][1] == 1.0
    assert pareto_auc_v1({"toy": pts}) == pytest.approx(0.4556, abs=1e-4)
    assert select_policy([V_A, V_B]) == 1


class BetaSwitch(LLMDesignedMethod):
    """Serial greedy (A) for β < 0.5, parallel (B) otherwise: one policy, two sweep points."""

    def solve(self, question, budget=None):
        question.reset()
        plan = ([["b0a0"], ["b0a1"], ["b0a2"]] if self.beta < 0.5
                else [["b0a0", "b1a0"], ["b0a1", "b1a1"]])
        for batch in plan:
            question.probe_batch(batch)
        return SimResult()


def test_toy_world_through_the_evaluator() -> None:
    cfg = EvalConfig(W=2, beta_grid=(0.2, 0.8), mode="xgen", root_policy="choose",
                     apply_plan=False)
    ev = evaluate_policy(BetaSwitch, [toy_world()], cfg)
    assert ev.pareto_auc == pytest.approx(0.4556, abs=1e-4)
    assert ev.parallel_penalty == pytest.approx(0.75)
    assert ev.pareto_reward == pytest.approx(pareto_reward(ev.pareto_auc, 0.75, 0.1))
    paper = evaluate_policy(BetaSwitch, [toy_world()],
                            EvalConfig(W=2, mode="paper", root_policy="choose", root_multi=True,
                                       beta_cost=0.01, beta_par=0.02, beta_grid=(0.2, 0.8)))
    assert paper.V == pytest.approx(0.70)          # baked default β 0.6 → policy B, raw scores


def test_child_semantics_choose_mode() -> None:
    q = ReplayQuestion(toy_world(), 3)
    assert q.legal_roots() == ["b0a0", "b1a0", "b2a0"] and q.opened_branches() == []
    out = q.probe_batch(["b1a0", "b2a0"])
    assert [o.cell_id for o in out] == ["b1a0", "b2a0"]
    assert q.opened_branches() == [1, 2]
    # opened b1 offers its unique recorded child; b2 has none (Child = ∅ → not legal)
    assert q.legal_actions() == ["b0a0", "b1a1"]
    assert q.meta("b1a1").seq == -1 and q.meta("b1a0").seq > 0
    with pytest.raises(KeyError):
        q.meta("b0a1")                              # neither revealed nor legal
    obs = q.probe_batch(["b1a1"])[0]
    assert obs.score == 0.70 and obs.delta_vs_parent == pytest.approx(0.40)
    assert set(q.observed()) == {"b1a0", "b2a0", "b1a1"}


def _seq_world() -> World:
    """b1 created before b0 (earliest-root order follows seq, not branch id)."""
    def o(b, a, s):
        return Observation(b, a, s, True, None, "ok", None, s - 0.1, None, cell_id=f"b{b}a{a}")
    cells = (WorldCell("b1a0", 1, 0, 1, None, o(1, 0, 0.3)),
             WorldCell("b0a0", 0, 0, 2, None, o(0, 0, 0.2)),
             WorldCell("b2a0", 2, 0, 3, None, o(2, 0, 0.4)),
             WorldCell("b0a1", 0, 1, 4, "b0a0", o(0, 1, 0.5)))
    return World("seq", 0.1, cells)


def test_child_semantics_earliest_mode() -> None:
    q = ReplayQuestion(_seq_world(), 2, "earliest")
    assert q.legal_roots() == [ROOT, ROOT, ROOT]
    assert q.meta(ROOT).branch == -1
    out = q.probe_batch([ROOT, ROOT])
    assert [o.cell_id for o in out] == ["b1a0", "b0a0"]       # earliest seq first
    assert q.legal_actions() == [ROOT, "b0a1"]
    assert replay_state(q)["revealed"] == ["b1a0", "b0a0"]
    single = ReplayQuestion(_seq_world(), 2, "earliest", root_multi=False)
    assert single.legal_roots() == [ROOT]
    with pytest.raises(ValueError):
        single.probe_batch([ROOT, ROOT])


def test_stop_conditions() -> None:
    w = toy_world()
    empty = run_episode(scripted(), w, None, 2)
    assert empty.episode.stop_reason == "empty_batch" and empty.episode.n_revealed == 0
    assert replay_value(empty.episode, beta_cost=0.01, beta_par=0.02,
                        norm=w.normalizer("paper")) == TOY_ROOT            # E15
    k2 = run_episode(SERIAL_A, w, None, 2, K2=2)
    assert k2.episode.rounds == 2 and k2.episode.stop_reason == "k2"
    every = scripted(["b0a0", "b1a0"], ["b0a1", "b1a1"], ["b0a2", "b2a0"])
    full = run_episode(every, w, None, 2)
    assert full.episode.stop_reason == "all_revealed" and full.episode.n_revealed == 6
    q = ReplayQuestion(w, 2, K2=1)
    q.probe_batch(["b0a0"])
    assert q.legal_actions() == [] and q.legal_roots() == []
    with pytest.raises(ValueError, match="over"):
        q.probe_batch(["b1a0"])


def test_reset_only_before_first_probe() -> None:
    q = ReplayQuestion(toy_world(), 2)
    q.reset()
    q.probe_batch(["b0a0"])
    with pytest.raises(RuntimeError, match="reset"):
        q.reset()


class Planner(LLMDesignedMethod):
    PLAN = GridPlan(2, 1, "test plan")

    def plan_grid(self, context):
        return self.PLAN

    def solve(self, question, budget=None):
        question.reset()
        while question.legal_actions():
            question.probe_batch(question.legal_actions()[: question.max_parallelism])
        return SimResult()


def test_grid_plan_support() -> None:
    w = toy_world()
    sub = run_episode(Planner, w, None, 3, apply_plan=True)
    assert sub.validity is not None and sub.validity.rewardable
    assert set(sub.revealed) == {"b0a0", "b0a1", "b1a0", "b1a1"}      # branches 0..1, attempts 0..1
    assert sub.episode.stop_reason == "all_revealed" and sub.episode.N_max == 6
    wide = type("Wide", (Planner,), {"PLAN": GridPlan(5, 1, "too wide")})
    out = run_episode(wide, w, None, 3, apply_plan=True)
    assert out.episode.stop_reason == "out_of_support" and out.episode.n_revealed == 0
    assert out.error is None and not out.validity.in_support
    huge = type("Huge", (Planner,), {"PLAN": GridPlan(2, 1, "x")})
    capped = run_episode(huge, w, None, 3, apply_plan=True, hard_max_branch_count=1)
    assert capped.episode.stop_reason == "invalid_plan"               # beyond the hard cap
    bad = type("Bad", (Planner,), {"PLAN": GridPlan(0, 1, "invalid")})
    assert run_episode(bad, w, None, 3, apply_plan=True).episode.stop_reason == "invalid_plan"
    none = type("NonePlan", (Planner,), {"plan_grid": lambda self, ctx: None})
    assert run_episode(none, w, None, 3, apply_plan=True).episode.stop_reason == "invalid_plan"


def test_replay_is_deterministic() -> None:
    loaded = load_policy(builtin_policy_source("portfolio"))
    for seed in (1, 4):
        w = synth_world(seed)
        runs = [run_episode(loaded, w, 0.4, 3) for _ in range(3)]
        assert runs[0].episode == runs[1].episode == runs[2].episode
        assert runs[0].rounds == runs[1].rounds == runs[2].rounds
        assert runs[0].episode.curve and runs[0].episode.curve[-1][0] == runs[0].episode.n_revealed


def test_policy_errors_are_recorded_not_raised() -> None:
    illegal = scripted(["b0a0"], ["b0a2"])
    run = run_episode(illegal, toy_world(), None, 2)
    assert run.error and "not legal" in run.error
    assert run.episode.stop_reason == "error" and run.episode.n_revealed == 1   # spent stays spent


def test_trace_round_records() -> None:
    run = run_episode(PARALLEL_B, toy_world(), 0.3, 2)
    r1, r2 = run.rounds
    assert r1["prefix"]["n_revealed"] == 0 and r1["prefix"]["n_legal"] == 3
    assert r2["prefix"]["branches"]["1"]["anchor"] == 0.30
    assert [x["cell_id"] for x in r2["revealed"]] == ["b0a1", "b1a1"]
    t = run.to_trace_json(role="x")
    assert t["probes"] == 4 and t["batch_sizes"] == [2, 2] and t["role"] == "x"
    assert len(t["decision_rounds"]) == 2
