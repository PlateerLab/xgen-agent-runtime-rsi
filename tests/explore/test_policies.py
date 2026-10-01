"""Built-in policies: π_1 parallel refine and the portfolio baseline."""

from __future__ import annotations

import pytest

from tests.explore.synthetic import synth_world, synth_worlds
from xgen_rsi.dream.evaluator import EvalConfig, evaluate_policy
from xgen_rsi.dream.replay import run_episode
from xgen_rsi.dream.sandbox import load_policy, static_check
from xgen_rsi.dream.world import World
from xgen_rsi.explore.api import GridPlanningContext, LiveCycleManifest
from xgen_rsi.explore.policies import (
    BUILTIN_POLICIES,
    ParallelRefinePolicy,
    PortfolioPolicy,
    builtin_policy_source,
)


@pytest.mark.parametrize("name", BUILTIN_POLICIES)
def test_builtin_sources_pass_the_sandbox(name: str) -> None:
    src = builtin_policy_source(name)
    assert static_check(src).ok
    loaded = load_policy(src, require=("solve", "plan_grid"))
    inst = loaded({"beta": 0.2})
    assert inst.beta == 0.2 and loaded({}).beta == 0.6


def test_parallel_refine_batches_frontiers_then_roots() -> None:
    w = World.from_branches({0: [0.1, 0.2, 0.3], 1: [0.2], 2: [0.3, 0.1], 3: [0.4, 0.5]},
                            root_score=0.0)
    run = run_episode(ParallelRefinePolicy, w, None, 2)
    rounds = [r["batch"] for r in run.rounds]
    assert rounds[0] == ["b0a0", "b1a0"]                  # W workspaces opened
    assert rounds[1] == ["b0a1", "b2a0"]                  # b1 exhausted → a new root fills
    assert run.episode.stop_reason == "all_revealed" and run.episode.n_revealed == w.N_max
    # β has no effect on π_1
    assert run_episode(ParallelRefinePolicy, w, 0.0, 2).revealed == run.revealed


def test_portfolio_is_deterministic_and_legal_on_many_worlds() -> None:
    loaded = load_policy(builtin_policy_source("portfolio"), require=("solve", "plan_grid"))
    for seed in range(8):
        w = synth_world(seed)
        for beta in (0.0, 0.5, 1.0):
            a = run_episode(loaded, w, beta, 3)
            b = run_episode(PortfolioPolicy, w, beta, 3)     # trusted class, same code
            assert a.error is None and b.error is None
            assert a.revealed == b.revealed and a.episode == b.episode
            assert all(1 <= s <= 3 for s in a.episode.batch_sizes)


def test_portfolio_beta_trades_work_for_attainment() -> None:
    worlds = synth_worlds(8)
    probes = {beta: sum(run_episode(PortfolioPolicy, w, beta, 4).episode.n_revealed
                        for w in worlds) for beta in (0.0, 1.0)}
    assert probes[0.0] < probes[1.0]


def test_portfolio_beats_parallel_refine_on_synthetic_worlds() -> None:
    worlds = synth_worlds(12)
    cfg = EvalConfig(W=4)
    pi1 = evaluate_policy(load_policy(builtin_policy_source("parallel_refine")), worlds, cfg)
    port = evaluate_policy(load_policy(builtin_policy_source("portfolio")), worlds, cfg)
    assert pi1.eligible and port.eligible
    # π_1 reveals everything: degenerate sweep, zero Pareto area
    assert pi1.pareto_auc == 0.0 and pi1.pareto_reward < 0
    assert port.pareto_reward > pi1.pareto_reward + 0.3
    assert port.V >= pi1.V                       # Eq.1 at the baked default β (0.6)
    works = [p["work"] for p in port.sweep["frontier"]]
    assert works[0] < works[-1]                  # the sweep is non-degenerate


def _ctx(history=(), trace=(None, None)) -> GridPlanningContext:
    return GridPlanningContext(hard_max_branch_count=16, hard_max_refine_count=12, worker_cap=4,
                               fallback_branch_count=8, fallback_refine_count=6,
                               trace_branch_count=trace[0], trace_refine_count=trace[1],
                               history=tuple(history))


def _m(it, **kw) -> LiveCycleManifest:
    base = dict(iteration=it, beta=0.6, best_score=0.5, effective_branch_count=8,
                effective_refine_count=6, opened_width=8, probes=40)
    base.update(kw)
    return LiveCycleManifest(**base)


def test_plan_grid_rules() -> None:
    p = PortfolioPolicy({})
    boot = p.plan_grid(_ctx())
    assert (boot.branch_count, boot.refine_count) == (8, 6) and "insufficient" in boot.reason
    assert (p.plan_grid(_ctx(trace=(5, 3))).branch_count, p.plan_grid(_ctx(trace=(5, 3))).refine_count) == (5, 3)
    widen = p.plan_grid(_ctx([_m(1, improving_roots=6, late_gain_branches=0)]))
    assert widen.branch_count == 10 and widen.refine_count == 6
    deepen = p.plan_grid(_ctx([_m(1, improving_roots=1, late_gain_branches=2)]))
    assert (deepen.branch_count, deepen.refine_count) == (8, 7)
    shrink = p.plan_grid(_ctx([_m(1, hard_failures=30)]))
    assert (shrink.branch_count, shrink.refine_count) == (6, 5)
    plateau = p.plan_grid(_ctx([_m(1, best_score=0.6, improving_roots=2, late_gain_branches=0,
                                   opened_width=8),
                                _m(2, best_score=0.6, improving_roots=2, late_gain_branches=0,
                                   opened_width=8)]))
    assert plateau.branch_count == 10
    hold = p.plan_grid(_ctx([_m(1, best_score=0.4), _m(2, best_score=0.6, improving_roots=3,
                                                      late_gain_branches=0, opened_width=8)]))
    assert (hold.branch_count, hold.refine_count) == (8, 6) and hold.reason
    # π_1 plans the fallback grid, clamped to the trace support in replay
    pi1 = ParallelRefinePolicy({}).plan_grid(_ctx(trace=(4, 9)))
    assert (pi1.branch_count, pi1.refine_count) == (4, 6)
