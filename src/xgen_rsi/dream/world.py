"""Replay worlds: frozen discovery trees in branch × attempt form (Dream-RSI §2–3).

A :class:`World` is one recorded tree 𝒯_i: the root r (score = baseline) and
a set of chains hanging from it. Because only the root and leaves are
eligible for expansion, every non-root node has at most one child, so the
tree is an irregular branch × attempt grid (03 §3.2). Cells carry the stored
:class:`~xgen_rsi.explore.api.Observation`, the creation order ``seq`` and the
branch's structural tags (e.g. the direction assigned by the direction
provider, E14).

Constructors
- :meth:`World.from_replay_nodes` — a recorder tree (``TrajectoryRecord.nodes``)
  or a ``LiveQuestion.nodes()`` tree. The root is the node tagged
  ``role == "explore_root"`` (or ``root_id``, or the unique parentless node);
  chains are followed by ``parent_id``; attempt = depth along the chain.
- :meth:`World.from_trial_outcomes` — the k evaluation trials of one task as k
  branches of depth 1 (34 §7). :func:`worlds_from_trial_outcomes` makes one
  world per (harness version, task), never mixing versions.
- :meth:`World.from_branches` — synthetic worlds (tests, toy examples).

:class:`WorldPool` is the append-only world collection 𝓗_t with the
development / selection split of 34 §7 (paper mode uses the same worlds for
both). Worlds and pools round-trip through JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from xgen_rsi.explore.api import GridPlanningContext, LiveCycleManifest, Observation, cell_id
from xgen_rsi.kernel.recorder import ReplayNode, TrajectoryRecord
from xgen_rsi.rsi_math import Normalizer, make_normalizer
from xgen_rsi.rsi_math.modes import Mode

_DROP_TAG_KEYS = frozenset({"role", "decision", "score", "reward", "fail_class", "error"})
"""Recorder tag keys that are bookkeeping or outcomes, never structural meta."""


def _freeze(v: Any) -> Any:
    if isinstance(v, Mapping):
        return MappingProxyType({str(k): _freeze(x) for k, x in v.items()})
    if isinstance(v, (list, tuple)):
        return tuple(_freeze(x) for x in v)
    if isinstance(v, (set, frozenset)):
        return tuple(sorted((_freeze(x) for x in v), key=repr))
    return v


def thaw(v: Any) -> Any:
    """A plain (mutable, JSON-ready) deep copy of a frozen tag/meta structure."""
    if isinstance(v, Mapping):
        return {k: thaw(x) for k, x in v.items()}
    if isinstance(v, tuple):
        return [thaw(x) for x in v]
    return v


@dataclass(frozen=True)
class WorldCell:
    """One recorded node of a world (a cell of the irregular grid)."""

    cell_id: str
    branch: int
    attempt: int
    seq: int
    parent_id: str | None
    obs: Observation
    tags: Mapping[str, Any] = field(default_factory=dict)
    node_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", _freeze(self.tags))

    def to_json(self) -> dict[str, Any]:
        return {"cell_id": self.cell_id, "branch": self.branch, "attempt": self.attempt,
                "seq": self.seq, "parent_id": self.parent_id, "node_id": self.node_id,
                "tags": thaw(self.tags), "obs": self.obs.to_json()}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> WorldCell:
        return cls(cell_id=d["cell_id"], branch=int(d["branch"]), attempt=int(d["attempt"]),
                   seq=int(d["seq"]), parent_id=d.get("parent_id"),
                   obs=Observation.from_json(d["obs"]), tags=d.get("tags") or {},
                   node_id=d.get("node_id"))


@dataclass(frozen=True)
class World:
    """An immutable replay world 𝒯_i (root + chains = irregular branch × attempt grid).

    ``root_score`` is s_r (None when the root was never scored);
    ``baseline_score`` is what ``question.baseline_score`` shows and the
    reference of the deltas (defaults to ``root_score``). ``meta`` holds
    provenance (task_id, harness_id, lineage, policy version, source,
    iteration, W, ...).
    """

    world_id: str
    root_score: float | None
    cells: tuple[WorldCell, ...]
    baseline_score: float | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)
    _branches: Mapping[int, tuple[WorldCell, ...]] = field(
        init=False, repr=False, compare=False, default_factory=dict)
    _by_id: Mapping[str, WorldCell] = field(init=False, repr=False, compare=False,
                                            default_factory=dict)

    def __post_init__(self) -> None:
        cells = tuple(sorted(self.cells, key=lambda c: c.seq))
        by_id: dict[str, WorldCell] = {}
        branches: dict[int, list[WorldCell]] = {}
        seqs: set[int] = set()
        for c in cells:
            if c.cell_id != cell_id(c.branch, c.attempt):
                raise ValueError(f"cell id {c.cell_id!r} is not canonical for "
                                 f"branch {c.branch}, attempt {c.attempt}")
            if c.cell_id in by_id:
                raise ValueError(f"duplicate cell {c.cell_id!r}")
            if c.seq in seqs:
                raise ValueError(f"duplicate seq {c.seq}")
            if c.branch < 0 or c.attempt < 0:
                raise ValueError(f"negative branch/attempt in {c.cell_id!r}")
            seqs.add(c.seq)
            by_id[c.cell_id] = c
            branches.setdefault(c.branch, []).append(c)
        frozen: dict[int, tuple[WorldCell, ...]] = {}
        for b, chain in branches.items():
            chain.sort(key=lambda c: c.attempt)
            for a, c in enumerate(chain):
                if c.attempt != a:
                    raise ValueError(f"branch {b} skips attempt {a}")
                want = cell_id(b, a - 1) if a > 0 else None
                if c.parent_id != want:
                    raise ValueError(f"cell {c.cell_id!r} has parent {c.parent_id!r}, want {want!r}")
                if a > 0 and c.seq <= chain[a - 1].seq:
                    raise ValueError(f"cell {c.cell_id!r} created before its parent")
            frozen[b] = tuple(chain)
        object.__setattr__(self, "cells", cells)
        object.__setattr__(self, "meta", _freeze(self.meta))
        object.__setattr__(self, "_branches", MappingProxyType(dict(sorted(frozen.items()))))
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))
        if self.baseline_score is None and self.root_score is not None:
            object.__setattr__(self, "baseline_score", self.root_score)

    # ------------------------------------------------------------- structure --

    @property
    def N_max(self) -> int:
        """Non-root nodes |𝒯_i| − 1."""
        return len(self.cells)

    @property
    def branches(self) -> Mapping[int, tuple[WorldCell, ...]]:
        """Branch → its cells in attempt order."""
        return self._branches

    @property
    def trace_branch_count(self) -> int:
        """B of the frozen trace: highest branch index + 1 (0 for an empty world)."""
        return (max(self._branches) + 1) if self._branches else 0

    @property
    def trace_refine_count(self) -> int:
        """R of the frozen trace: highest attempt index (0 for an empty world)."""
        return max((c.attempt for c in self.cells), default=0)

    def cell(self, cid: str) -> WorldCell:
        return self._by_id[cid]

    @property
    def cell_ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(c.node_id for c in self.cells if c.node_id)

    def scores(self) -> list[float]:
        """Recorded cell scores (unscored cells skipped)."""
        return [c.obs.score for c in self.cells if c.obs.score is not None]

    # ----------------------------------------------------------- scoring ---

    @property
    def norm_baseline(self) -> float:
        """s_base,i of E8: the root score, else the baseline score, else 0.0."""
        if self.root_score is not None:
            return float(self.root_score)
        if self.baseline_score is not None:
            return float(self.baseline_score)
        return 0.0

    def normalizer(self, mode: Mode = "xgen") -> Normalizer:
        """s̃ of E8 for this world (identity in paper mode), via ``rsi_math.make_normalizer``."""
        pool = self.scores()
        if self.root_score is not None:
            pool.append(float(self.root_score))
        return make_normalizer(pool, self.norm_baseline, mode)

    @property
    def floor_score(self) -> float:
        """Stand-in s_v for an unscored revealed cell: the world's lowest score
        (never raises the replay max above what the world otherwise allows)."""
        return min([self.norm_baseline, *self.scores()])

    def planning_context(self, *, W: int, hard_max_branch_count: int | None = None,
                         hard_max_refine_count: int | None = None,
                         history: Sequence[LiveCycleManifest] = ()) -> GridPlanningContext:
        """The replay ``GridPlanningContext`` of this world (trace support fields set).

        Hard caps default to a generous runtime bound (``max(64, 4·B)`` and
        ``max(64, 4·(R+1))``) so that a plan beyond the trace reads as *out of
        support* rather than invalid. ``history`` keeps only manifests of live
        cycles completed before this world's iteration (``meta["iteration"]``);
        unknown iteration → none.
        """
        it = self.meta.get("iteration")
        hist = tuple(m for m in history if it is not None and m.iteration < int(it))
        B, R = max(1, self.trace_branch_count), self.trace_refine_count
        hb = int(hard_max_branch_count) if hard_max_branch_count else max(64, 4 * B)
        hr = int(hard_max_refine_count) if hard_max_refine_count else max(64, 4 * (R + 1))
        return GridPlanningContext(
            hard_max_branch_count=hb, hard_max_refine_count=hr,
            worker_cap=int(W), fallback_branch_count=B, fallback_refine_count=R,
            trace_branch_count=self.trace_branch_count, trace_refine_count=R, history=hist)

    # ----------------------------------------------------------------- JSON --

    def to_json(self) -> dict[str, Any]:
        return {"world_id": self.world_id, "root_score": self.root_score,
                "baseline_score": self.baseline_score, "meta": thaw(self.meta),
                "cells": [c.to_json() for c in self.cells]}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> World:
        return cls(world_id=str(d["world_id"]), root_score=d.get("root_score"),
                   baseline_score=d.get("baseline_score"), meta=d.get("meta") or {},
                   cells=tuple(WorldCell.from_json(c) for c in d.get("cells") or ()))

    def save(self, path: str | os.PathLike[str]) -> None:
        _atomic_write(Path(path), json.dumps(self.to_json(), ensure_ascii=False, indent=1))

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> World:
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    # ---------------------------------------------------------- constructors --

    @classmethod
    def from_branches(cls, branches: Mapping[int, Sequence[Any]], *, root_score: float | None,
                      baseline_score: float | None = None, world_id: str = "world",
                      tags: Mapping[int, Mapping[str, Any]] | None = None,
                      seq_order: str = "round_robin",
                      meta: Mapping[str, Any] | None = None) -> World:
        """A synthetic world from per-branch cell specs.

        A spec is a float (successful evaluation with that score), None (an
        unevaluated environment failure), an :class:`Observation`, or a
        mapping of Observation fields (``evaluated`` defaults to True,
        ``fail_class`` to ``"ok"``). ``seq_order`` is ``"round_robin"``
        (attempt-major, the creation order of a parallel-refine run) or
        ``"branch_major"``.
        """
        base = baseline_score if baseline_score is not None else root_score
        positions = [(b, a) for b, specs in branches.items() for a in range(len(specs))]
        if seq_order == "round_robin":
            positions.sort(key=lambda p: (p[1], p[0]))
        elif seq_order == "branch_major":
            positions.sort()
        else:
            raise ValueError(f"unknown seq_order {seq_order!r}")
        seq_of = {p: i + 1 for i, p in enumerate(positions)}
        cells: list[WorldCell] = []
        for b, specs in branches.items():
            parent_score = base
            for a, spec in enumerate(specs):
                obs = _obs_from_spec(spec, b, a, base, parent_score)
                cells.append(WorldCell(cell_id=cell_id(b, a), branch=int(b), attempt=a,
                                       seq=seq_of[(b, a)],
                                       parent_id=cell_id(b, a - 1) if a > 0 else None,
                                       obs=obs, tags=dict((tags or {}).get(b, {}))))
                parent_score = obs.score
        return cls(world_id=world_id, root_score=root_score, baseline_score=base,
                   cells=tuple(cells), meta=dict(meta or {"source": "synthetic"}))

    @classmethod
    def from_replay_nodes(cls, nodes: Iterable[ReplayNode | Mapping[str, Any]], *,
                          root_id: str | None = None, world_id: str | None = None,
                          baseline_score: float | None = None,
                          meta: Mapping[str, Any] | None = None) -> World:
        """Build a world from recorded tree nodes (TrajectoryRecord.nodes / LiveQuestion.nodes()).

        Raises ``ValueError`` when the subtree under the root is not a set of
        chains (a non-root node with several children).
        """
        items = [n if isinstance(n, ReplayNode) else ReplayNode(**dict(n)) for n in nodes]
        by_id = {n.node_id: n for n in items}
        if len(by_id) != len(items):
            raise ValueError("duplicate node ids")
        children: dict[str | None, list[ReplayNode]] = {}
        for n in items:
            children.setdefault(n.parent_id, []).append(n)
        for kids in children.values():
            kids.sort(key=lambda n: n.created_seq)
        root = _find_root(items, by_id, root_id)
        root_score = root.score if root.score is not None else None
        base = baseline_score if baseline_score is not None else root_score
        heads = children.get(root.node_id, [])
        explicit = [h.branch for h in heads]
        use_explicit = (len(set(explicit)) == len(explicit)
                        and all(isinstance(b, int) and b >= 0 for b in explicit))
        cells: list[WorldCell] = []
        for i, head in enumerate(heads):
            b = head.branch if use_explicit else i
            cur, a, parent_score = head, 0, base
            while True:
                obs = Observation(
                    branch=b, attempt=a, score=cur.score, evaluated=bool(cur.evaluated),
                    valid=cur.valid, fail_class=cur.fail_class or "ok", error=cur.error,
                    delta_vs_baseline=_delta(cur.score, base),
                    delta_vs_parent=_delta(cur.score, parent_score),
                    n_valid=cur.n_valid, n_total=cur.n_total, cell_id=cell_id(b, a))
                tags = {k: v for k, v in (cur.tags or {}).items() if k not in _DROP_TAG_KEYS}
                cells.append(WorldCell(cell_id=cell_id(b, a), branch=b, attempt=a,
                                       seq=int(cur.created_seq),
                                       parent_id=cell_id(b, a - 1) if a > 0 else None,
                                       obs=obs, tags=tags, node_id=cur.node_id))
                kids = children.get(cur.node_id, [])
                if not kids:
                    break
                if len(kids) > 1:
                    raise ValueError(
                        f"node {cur.node_id!r} has {len(kids)} children: the subtree is not a "
                        "branch x attempt grid (pass root_id of an explore sub-tree)")
                cur, a, parent_score = kids[0], a + 1, cur.score
        m: dict[str, Any] = {"source": "replay_nodes", "tree_id": root.tree_id,
                             "root_node_id": root.node_id}
        if isinstance(root.tags, Mapping) and "W" in root.tags:
            m["W"] = root.tags["W"]
        m.update(dict(meta or {}))
        return cls(world_id=world_id or f"{root.tree_id[:12]}:{root.node_id}",
                   root_score=root_score, baseline_score=base, cells=tuple(cells), meta=m)

    @classmethod
    def from_trial_outcomes(cls, task_id: str, outcomes: Iterable[Any], *, harness_id: str = "",
                            baseline_score: float | None = 0.0, world_id: str | None = None,
                            meta: Mapping[str, Any] | None = None) -> World:
        """The evaluation trials of one task (``evolve.runner.TrialOutcome``) as
        branches of depth 1: branch j = trial j, root score = ``baseline_score``.

        Cell observations come from :func:`outcome_observation_fields`, the
        same mapping live discovery uses (``xgen_rsi.discovery``).
        """
        mine = sorted((o for o in outcomes if o.task_id == task_id), key=lambda o: o.trial)
        if not mine:
            raise ValueError(f"no outcomes for task {task_id!r}")
        trials = [int(o.trial) for o in mine]
        if len(set(trials)) != len(trials):
            raise ValueError(f"task {task_id!r}: duplicate trial numbers (mixed harness versions?)")
        cells: list[WorldCell] = []
        for i, o in enumerate(mine):
            b = int(o.trial)
            f = outcome_observation_fields(o)
            score = f["score"]
            obs = Observation(branch=b, attempt=0, delta_vs_baseline=_delta(score, baseline_score),
                              delta_vs_parent=_delta(score, baseline_score), cell_id=cell_id(b, 0), **f)
            cells.append(WorldCell(cell_id=cell_id(b, 0), branch=b, attempt=0, seq=i + 1,
                                   parent_id=None, obs=obs, tags={}, node_id=o.record))
        m: dict[str, Any] = {"source": "trials", "task_id": task_id, "harness_id": harness_id,
                             "k": len(mine)}
        m.update(dict(meta or {}))
        return cls(world_id=world_id or f"trials:{harness_id or 'h'}:{task_id}",
                   root_score=baseline_score, baseline_score=baseline_score,
                   cells=tuple(cells), meta=m)


def outcome_observation_fields(outcome: Any) -> dict[str, Any]:
    """Observation fields of one verified attempt (``evolve.runner.TrialOutcome``).

    The one mapping shared by evaluation-trial worlds and live discovery, so a task looks the same
    to a policy online and in replay:

    - a missing attempt (infrastructure failure) is unevaluated ``infra_error`` with no score;
    - an attempt that produced no deliverable is ``no_submission`` (evaluated, scored);
    - every other attempt is evaluated ``ok`` with score = verifier reward, *including partial
      credit*: the reward is the fraction of checks passed, so a partial result is progress a
      policy must see, not a failure (unlike a kernel benchmark, where a wrong output has no score).

    ``n_valid`` / ``n_total`` are the passed / total checks.
    """
    ver = outcome.verifier if isinstance(outcome.verifier, Mapping) else {}
    checks = ver.get("checks") or []
    n_total = len(checks) if checks else None
    n_valid = sum(1 for c in checks if isinstance(c, Mapping) and c.get("passed")) if checks else None
    if outcome.missing:
        score, evaluated, fail, err = None, False, "infra_error", (outcome.error or "missing trial")[:500]
    elif outcome.no_submission:
        score, evaluated, fail, err = float(outcome.reward), True, "no_submission", "no submission"
    else:
        score, evaluated, fail, err = float(outcome.reward), True, "ok", None
    return {"score": score, "evaluated": evaluated, "valid": outcome.valid_output, "fail_class": fail,
            "error": err, "n_valid": n_valid, "n_total": n_total}


def _delta(score: float | None, ref: float | None) -> float | None:
    return None if score is None or ref is None else float(score) - float(ref)


def _obs_from_spec(spec: Any, b: int, a: int, base: float | None,
                   parent_score: float | None) -> Observation:
    if isinstance(spec, Observation):
        d = spec.to_json()
    elif spec is None:
        d = {"score": None, "evaluated": False, "fail_class": "env_failure",
             "error": "not evaluated"}
    elif isinstance(spec, Mapping):
        d = dict(spec)
    else:
        d = {"score": float(spec)}
    score = d.get("score")
    score = None if score is None else float(score)
    return Observation(branch=b, attempt=a, score=score, evaluated=bool(d.get("evaluated", True)),
                       valid=d.get("valid"), fail_class=str(d.get("fail_class") or "ok"),
                       error=d.get("error"), delta_vs_baseline=_delta(score, base),
                       delta_vs_parent=_delta(score, parent_score), n_valid=d.get("n_valid"),
                       n_total=d.get("n_total"), cell_id=cell_id(b, a))


def _find_root(items: list[ReplayNode], by_id: Mapping[str, ReplayNode],
               root_id: str | None) -> ReplayNode:
    if root_id is not None:
        if root_id not in by_id:
            raise ValueError(f"root node {root_id!r} not found")
        return by_id[root_id]
    explore = [n for n in items if isinstance(n.tags, Mapping) and n.tags.get("role") == "explore_root"]
    if len(explore) == 1:
        return explore[0]
    if len(explore) > 1:
        raise ValueError("several explore roots: pass root_id or use worlds_from_record")
    roots = [n for n in items if n.parent_id is None or n.parent_id not in by_id]
    if len(roots) != 1:
        raise ValueError(f"expected exactly one root node, found {len(roots)}")
    return roots[0]


def worlds_from_record(record: TrajectoryRecord) -> list[World]:
    """All replay worlds of one trajectory record.

    One world per explore sub-tree (nodes tagged ``role == "explore_root"``).
    Without explore sub-trees the whole tree is used, but only when at least
    one node was evaluated (an unscored conversation chain is no world).
    """
    meta = {"task_id": record.task_id, "harness_id": record.harness_id,
            "lineage": record.lineage, "explore_policy_id": record.explore_policy_id,
            "trajectory_id": record.trajectory_id, "source": "recorder"}
    roots = [n for n in record.nodes if isinstance(n.tags, Mapping)
             and n.tags.get("role") == "explore_root"]
    if roots:
        return [World.from_replay_nodes(record.nodes, root_id=r.node_id, meta=meta,
                                        world_id=f"{record.trajectory_id}:{r.node_id}")
                for r in roots]
    if not any(n.evaluated for n in record.nodes):
        return []
    return [World.from_replay_nodes(record.nodes, meta=meta, world_id=record.trajectory_id)]


def worlds_from_trial_outcomes(outcomes_by_harness: Mapping[str, Sequence[Any]], *,
                               baseline_score: float | None = 0.0) -> list[World]:
    """One world per (harness version, task): versions are never mixed in one world.

    ``outcomes_by_harness`` maps a harness id to its outcomes (e.g.
    ``evolve.runner.load_outcomes(out_dir)`` of that candidate's eval dir).
    """
    worlds: list[World] = []
    for harness_id in sorted(outcomes_by_harness):
        outs = list(outcomes_by_harness[harness_id])
        for task_id in sorted({o.task_id for o in outs}):
            worlds.append(World.from_trial_outcomes(task_id, outs, harness_id=harness_id,
                                                    baseline_score=baseline_score))
    return worlds


# ------------------------------------------------------------------- pool --


@dataclass(frozen=True)
class PoolSplit:
    """Development worlds (policy-dev feedback) and selection worlds (m★)."""

    dev: WorldPool
    selection: WorldPool
    shared: bool


class WorldPool:
    """Append-only world collection 𝓗_t (𝓗_t = 𝓗_{t−1} ∪ {𝒯_t})."""

    def __init__(self, worlds: Iterable[World] = ()) -> None:
        ws = tuple(worlds)
        ids = [w.world_id for w in ws]
        if len(set(ids)) != len(ids):
            raise ValueError("world ids must be unique within a pool")
        self._worlds = ws

    def __iter__(self) -> Iterator[World]:
        return iter(self._worlds)

    def __len__(self) -> int:
        return len(self._worlds)

    def __getitem__(self, i: int) -> World:
        return self._worlds[i]

    @property
    def worlds(self) -> tuple[World, ...]:
        return self._worlds

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(w.world_id for w in self._worlds)

    def get(self, world_id: str) -> World:
        for w in self._worlds:
            if w.world_id == world_id:
                return w
        raise KeyError(world_id)

    def add(self, *worlds: World) -> WorldPool:
        """A new pool with ``worlds`` appended (the original is unchanged)."""
        return WorldPool((*self._worlds, *worlds))

    def split(self, mode: Mode = "xgen", *, selection_fraction: float = 0.5) -> PoolSplit:
        """Dev/selection split of 34 §7.

        paper: both sides are the whole pool. xgen: worlds are grouped by
        ``meta["task_id"]`` (else world id) so one task never sits on both
        sides, groups are ordered by a SHA-256 of the group key and the last
        ``round(n · selection_fraction)`` groups (≥ 1) go to selection. With
        fewer than two groups both sides share the pool (``shared=True``).
        """
        if mode == "paper":
            return PoolSplit(self, self, True)
        if not 0.0 < selection_fraction < 1.0:
            raise ValueError("selection_fraction must lie in (0, 1)")
        groups: dict[str, list[World]] = {}
        for w in self._worlds:
            key = str(w.meta.get("task_id") or w.world_id)
            groups.setdefault(key, []).append(w)
        if len(groups) < 2:
            return PoolSplit(self, self, True)
        order = sorted(groups, key=lambda k: hashlib.sha256(k.encode("utf-8")).hexdigest())
        n_sel = min(len(order) - 1, max(1, round(len(order) * selection_fraction)))
        sel_keys = set(order[len(order) - n_sel:])
        dev = [w for w in self._worlds if str(w.meta.get("task_id") or w.world_id) not in sel_keys]
        sel = [w for w in self._worlds if str(w.meta.get("task_id") or w.world_id) in sel_keys]
        return PoolSplit(WorldPool(dev), WorldPool(sel), False)

    def to_json(self) -> dict[str, Any]:
        return {"worlds": [w.to_json() for w in self._worlds]}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> WorldPool:
        return cls(World.from_json(w) for w in d.get("worlds") or ())

    def save(self, path: str | os.PathLike[str]) -> None:
        _atomic_write(Path(path), json.dumps(self.to_json(), ensure_ascii=False))

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> WorldPool:
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


__all__ = ["PoolSplit", "World", "WorldCell", "WorldPool", "outcome_observation_fields", "thaw", "worlds_from_record",
           "worlds_from_trial_outcomes"]
