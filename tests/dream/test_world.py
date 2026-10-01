"""Worlds from recorder trees, live grids and evaluation trials; pools, split and JSON."""

from __future__ import annotations

import asyncio

import pytest

from tests.explore.synthetic import toy_world
from xgen_rsi.dream.world import (
    World,
    WorldCell,
    WorldPool,
    worlds_from_record,
    worlds_from_trial_outcomes,
)
from xgen_rsi.evolve.runner import TrialOutcome
from xgen_rsi.explore.api import CellMeta, GridPlan, Observation
from xgen_rsi.explore.grid import LiveGrid, LiveQuestion
from xgen_rsi.kernel.recorder import ReplayNode, TrajectoryRecorder


def _recorder() -> TrajectoryRecorder:
    return TrajectoryRecorder(task_id="task-a", harness_id="h1", harness_name="h0",
                              lineage="openai/gpt", provider="openai", model="m",
                              thinking_level=None, explore_policy_id="pi0")


def test_plain_turn_chain_is_no_world_until_scored() -> None:
    rec = _recorder()
    for i in range(3):
        rec.step(attempt=i, tool_calls=1, tool_errors=0, decision="continue", policy_tokens=10,
                 model_calls=1)
    assert worlds_from_record(rec.record) == []
    for n, s in zip(rec.record.nodes, (0.1, 0.2, 0.4, 0.3)):
        n.score, n.evaluated = s, True
    (w,) = worlds_from_record(rec.record)
    assert w.root_score == 0.1 and w.N_max == 3 and w.trace_branch_count == 1
    assert [c.cell_id for c in w.cells] == ["b0a0", "b0a1", "b0a2"]
    assert w.cell("b0a1").obs.delta_vs_parent == pytest.approx(0.2)
    assert w.cell("b0a0").tags == {}                         # role/decision tags dropped
    assert w.meta["task_id"] == "task-a" and w.meta["harness_id"] == "h1"


def test_explore_subtree_inside_a_record() -> None:
    rec = _recorder()
    rec.step(attempt=0, tool_calls=0, tool_errors=0, decision="continue", policy_tokens=1,
             model_calls=1)
    tip = rec.record.nodes[-1].node_id

    async def attempt(meta: CellMeta, parent, tags) -> Observation:
        return Observation(branch=0, attempt=0, score=0.5 + 0.1 * meta.attempt - 0.2 * meta.branch,
                           evaluated=True, valid=None, fail_class="ok", error=None,
                           delta_vs_baseline=None, delta_vs_parent=None)

    q = LiveQuestion(LiveGrid(GridPlan(2, 1), tree_id=rec.record.tree_id, parent_node_id=tip,
                              directions=[{"direction": "x"}]), attempt, W=2, baseline_score=0.4)
    q.probe_batch(["b0a0", "b1a0"])
    q.probe_batch(["b0a1"])
    rec.add_nodes(q.nodes())
    rec.step(attempt=1, tool_calls=0, tool_errors=0, decision="complete", policy_tokens=1,
             model_calls=1)
    # the whole record tree branches at the tip (main chain + explore grid) …
    with pytest.raises(ValueError, match="explore|children|root"):
        World.from_replay_nodes(rec.record.nodes, root_id=rec.record.nodes[0].node_id)
    # … but the explore sub-tree is a proper world
    (w,) = worlds_from_record(rec.record)
    assert w.root_score == 0.4 and w.N_max == 3
    assert w.cell("b0a1").obs.score == pytest.approx(0.6)
    assert w.cell("b0a0").tags == {"direction": "x"}
    assert w.meta["W"] == 2 and w.meta["trajectory_id"] == rec.record.trajectory_id
    assert asyncio.iscoroutinefunction(attempt)


def test_non_chain_tree_rejected_and_dict_nodes_accepted() -> None:
    nodes = [ReplayNode("r", "t", None, 0, 0, 1, score=0.2, evaluated=True),
             ReplayNode("a", "t", "r", 0, 0, 2, score=0.3, evaluated=True),
             ReplayNode("b", "t", "a", 0, 1, 3, score=0.4, evaluated=True),
             ReplayNode("c", "t", "a", 0, 1, 4, score=0.5, evaluated=True)]
    with pytest.raises(ValueError, match="children"):
        World.from_replay_nodes(nodes)
    ok = World.from_replay_nodes([n.__dict__ for n in nodes[:3]])
    assert ok.N_max == 2 and ok.trace_refine_count == 1


def _outcome(task: str, trial: int, reward: float, *, missing: bool = False,
             passed: int = 3, total: int = 3) -> TrialOutcome:
    checks = [{"passed": i < passed} for i in range(total)]
    return TrialOutcome(task_id=task, trial=trial, reward=reward, weight=float(total), tokens=100,
                        missing=missing, valid_output=None if missing else passed == total,
                        no_submission=False, status="complete", answer="", record=f"rec-{trial}",
                        verifier={"fail_class": "ok" if passed == total else "wrong_output",
                                  "checks": checks},
                        error="infra down" if missing else "")


def test_worlds_from_trial_outcomes() -> None:
    outs = [_outcome("t1", 0, 1.0), _outcome("t1", 1, 1 / 3, passed=1),
            _outcome("t1", 2, 0.0, missing=True), _outcome("t2", 0, 0.5, passed=1, total=2)]
    w = World.from_trial_outcomes("t1", outs, harness_id="h7")
    assert w.N_max == 3 and w.trace_refine_count == 0 and w.trace_branch_count == 3
    assert w.root_score == 0.0 and w.meta["source"] == "trials"
    good, partial, missing = (w.cell(f"b{j}a0").obs for j in range(3))
    assert good.fail_class == "ok" and (good.n_valid, good.n_total) == (3, 3)
    # partial credit is an evaluated attempt with a score (the same mapping as live discovery)
    assert partial.fail_class == "ok" and partial.score == 1 / 3 and partial.n_valid == 1
    assert missing.fail_class == "infra_error" and not missing.evaluated and missing.score is None
    assert w.cell("b1a0").node_id == "rec-1"
    # one world per (harness version, task)
    worlds = worlds_from_trial_outcomes({"h1": outs, "h2": outs[:2]})
    assert [x.world_id for x in worlds] == ["trials:h1:t1", "trials:h1:t2", "trials:h2:t1"]
    with pytest.raises(ValueError, match="duplicate trial"):
        World.from_trial_outcomes("t1", outs + [_outcome("t1", 0, 0.2)])
    with pytest.raises(ValueError, match="no outcomes"):
        World.from_trial_outcomes("t9", outs)


def test_world_validation_normalizer_and_json(tmp_path) -> None:
    w = toy_world()
    assert (w.N_max, w.trace_branch_count, w.trace_refine_count) == (6, 3, 2)
    norm = w.normalizer()
    assert norm(0.40) == 0.0 and norm(0.70) == 1.0 and norm(0.30) == 0.0
    assert w.normalizer("paper")(0.62) == 0.62
    assert w.floor_score == pytest.approx(0.30)
    w.save(tmp_path / "w.json")
    assert World.load(tmp_path / "w.json").to_json() == w.to_json()
    obs = w.cell("b0a0").obs
    with pytest.raises(ValueError, match="skips"):
        World("bad", 0.0, (WorldCell("b0a1", 0, 1, 1, "b0a0", obs),))
    with pytest.raises(ValueError, match="canonical"):
        World("bad", 0.0, (WorldCell("x", 0, 0, 1, None, obs),))
    with pytest.raises(TypeError):
        w.cell("b0a0").tags["k"] = 1                       # frozen structure


def test_pool_split_and_roundtrip(tmp_path) -> None:
    ws = [World.from_branches({0: [0.1 * i]}, root_score=0.0, world_id=f"w{i}",
                              meta={"task_id": f"task{i % 3}"}) for i in range(6)]
    pool = WorldPool(ws)
    paper = pool.split("paper")
    assert paper.shared and paper.dev is pool and paper.selection is pool
    split = pool.split("xgen")
    assert not split.shared and len(split.dev) + len(split.selection) == 6
    dev_tasks = {w.meta["task_id"] for w in split.dev}
    sel_tasks = {w.meta["task_id"] for w in split.selection}
    assert dev_tasks and sel_tasks and not dev_tasks & sel_tasks
    assert pool.split("xgen").dev.ids == split.dev.ids              # deterministic
    single = WorldPool(ws[:1]).split("xgen")
    assert single.shared
    pool.save(tmp_path / "pool.json")
    assert WorldPool.load(tmp_path / "pool.json").to_json() == pool.to_json()
    bigger = pool.add(toy_world())
    assert len(bigger) == 7 and len(pool) == 6
    with pytest.raises(ValueError, match="unique"):
        bigger.add(toy_world())
