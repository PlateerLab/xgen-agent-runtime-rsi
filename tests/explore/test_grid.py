"""LiveGrid / LiveQuestion: async attempts, at most W concurrent, recorded as a tree."""

from __future__ import annotations

import asyncio

import pytest

from xgen_rsi.dream.replay import run_episode
from xgen_rsi.dream.world import World
from xgen_rsi.explore.api import CellMeta, GridPlan, Observation
from xgen_rsi.explore.grid import AttemptResult, LiveGrid, LiveQuestion, solve_live, summarize_live
from xgen_rsi.explore.policies import ParallelRefinePolicy, PortfolioPolicy


def _score(meta: CellMeta) -> float:
    return round(0.3 + 0.1 * meta.branch + 0.05 * meta.attempt, 6)


def _make_attempt(log: list[tuple[str, object]], delay: float = 0.01):
    async def attempt(meta: CellMeta, parent: Observation | None, tags) -> AttemptResult:
        log.append((f"b{meta.branch}a{meta.attempt}", parent.cell_id if parent else None))
        await asyncio.sleep(delay)
        if meta.branch == 1 and meta.attempt == 1:
            raise RuntimeError("sandbox crashed")
        obs = Observation(branch=-9, attempt=-9, score=_score(meta), evaluated=True, valid=True,
                          fail_class="ok", error=None, delta_vs_baseline=None,
                          delta_vs_parent=None, n_valid=1, n_total=1)
        return AttemptResult(obs, diagnostics={"dir": tags.get("direction")}, policy_tokens=7,
                             model_calls=1)
    return attempt


async def test_live_question_runs_policy_and_records_tree() -> None:
    grid = LiveGrid(GridPlan(3, 2), directions=[{"direction": d} for d in ("tiling", "fusion")],
                    parent_node_id="tip-node")
    log: list[tuple[str, object]] = []
    q = LiveQuestion(grid, _make_attempt(log), W=3, baseline_score=0.25)
    await solve_live(ParallelRefinePolicy({"beta": 0.5}), q)
    obs = q.observed()
    assert len(obs) == 9 and q.rounds == 3 and q.batch_sizes == [3, 3, 3]
    assert 1 < q.max_inflight <= 3
    # the live question owns branch/attempt/deltas; failures become observations
    o = obs["b2a1"]
    assert (o.branch, o.attempt, o.cell_id) == (2, 1, "b2a1")
    assert o.delta_vs_baseline == pytest.approx(_score(CellMeta(2, 1, None, 0)) - 0.25)
    assert o.delta_vs_parent == pytest.approx(0.05)
    bad = obs["b1a1"]
    assert bad.fail_class == "env_failure" and not bad.evaluated and "crashed" in bad.error
    assert obs["b1a2"].delta_vs_parent is None            # parent unscored
    # parents are passed to attempt_fn
    assert ("b0a1", "b0a0") in log and ("b0a0", None) in log
    # the recorded tree: explore root + one node per attempt, chains by parent id
    nodes = q.nodes()
    root = nodes[0]
    assert root.tags["role"] == "explore_root" and root.parent_id == "tip-node"
    assert root.score == 0.25
    by_id = {n.node_id: n for n in nodes}
    for n in nodes[1:]:
        parent = by_id[n.parent_id]
        assert parent.node_id == root.node_id if n.attempt == 0 else parent.attempt == n.attempt - 1
        crashed = n.fail_class == "env_failure"
        assert n.policy_tokens == (0 if crashed else 7)
    assert by_id[q.nodes()[1].node_id].tags.get("direction") == "tiling"
    # direction tags as structural meta
    assert q.meta("b1a0").tags == {"direction": "fusion"} and q.meta("b2a0").tags == {}
    # the tree is a replay world: the same policy replays the same reveal order
    world = World.from_replay_nodes(nodes, world_id="live")
    assert world.root_score == 0.25 and world.N_max == 9 and world.meta["W"] == 3
    assert world.cell("b2a1").obs == o
    run = run_episode(ParallelRefinePolicy, world, 0.5, 3)
    assert run.revealed == tuple(obs) and run.episode.stop_reason == "all_revealed"


def test_live_question_sync_path_and_guards() -> None:
    q = LiveQuestion(LiveGrid(GridPlan(2, 1)), _make_attempt([], 0.0), W=2, baseline_score=None)
    q.reset()                                   # allowed before probing
    out = q.probe_batch(["b0a0", "b1a0"], on_reveal=None)
    assert [o.cell_id for o in out] == ["b0a0", "b1a0"]
    assert out[0].delta_vs_baseline is None
    with pytest.raises(RuntimeError, match="reset"):
        q.reset()
    with pytest.raises(ValueError):
        q.probe_batch(["b0a1", "b0a1"])
    with pytest.raises(ValueError, match="not legal"):
        q.probe_batch(["b0a2"])                 # outside the grid (R = 1)
    with pytest.raises(KeyError):
        q.meta("b5a0")
    assert q.meta("b0a1").seq == -1 and q.meta("b0a0").seq == 1


async def test_probe_inside_loop_without_binding_is_refused() -> None:
    q = LiveQuestion(LiveGrid(GridPlan(1, 0)), _make_attempt([], 0.0), W=1)
    with pytest.raises(RuntimeError, match="running event loop"):
        q.probe_batch(["b0a0"])
    out = await q.aprobe_batch(["b0a0"])
    assert out[0].score == pytest.approx(0.3)


async def test_portfolio_live_and_manifest() -> None:
    grid = LiveGrid(GridPlan(4, 3))
    q = LiveQuestion(grid, _make_attempt([], 0.0), W=2, baseline_score=0.2)
    await solve_live(PortfolioPolicy({"beta": 0.6}), q)
    assert all(size <= 2 for size in q.batch_sizes)
    m = summarize_live(q, iteration=3, beta=0.6, policy_version="r0001_dev")
    assert m.iteration == 3 and m.probes == len(q.observed()) and m.rounds == q.rounds
    assert m.effective_branch_count == 4 and m.effective_refine_count == 3
    assert m.best_score == max(o.score for o in q.observed().values() if o.fail_class == "ok")
    assert m.opened_width >= 1 and m.W == 2
    assert m.improving_roots >= 1
