"""Live exploration grid: a :class:`~xgen_rsi.explore.api.Question` whose
transition runs real attempts (31 §5.2).

``LiveGrid(plan, directions=...)`` is the hard bound of one live episode:
branches ``0..B−1`` and attempts ``0..R`` (``GridPlan``), with direction tags
per branch (E14, the direction provider's output) exposed as ``meta().tags``.

``LiveQuestion(grid, attempt_fn, W=...)`` implements the Question protocol.
``probe_batch`` validates the batch with the shared legality rules
(:func:`~xgen_rsi.explore.api.check_batch`) and runs one asynchronous
``attempt_fn(cell_meta, parent_observation_or_None, direction_tags)`` per
cell, at most ``W`` at a time. Each completed attempt becomes a
:class:`~xgen_rsi.kernel.recorder.ReplayNode` under the grid's root node, so
the episode is recorded as a discovery tree (a future replay world).

Threading model (pure asyncio, no kernel dependency): a policy's ``solve`` is
synchronous. Inside an async host run it through :func:`solve_live`, which
binds the running loop and executes ``solve`` in a worker thread; each
``probe_batch`` then schedules the attempts on that loop and blocks the
worker thread only. Without a bound loop, ``probe_batch`` runs the batch with
``asyncio.run`` in the calling thread (it refuses to run inside a running
loop, which would deadlock).

Information policy (same as replay): ``meta().seq`` is the creation order for
revealed cells and −1 for legal cells that do not exist yet.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from xgen_rsi.kernel.recorder import ReplayNode

from .api import (
    CellMeta,
    GridPlan,
    LiveCycleManifest,
    Observation,
    cell_id,
    check_batch,
)
from .signals import failure_kind, is_success


@dataclass(frozen=True)
class AttemptResult:
    """Optional richer return value of an ``attempt_fn``: the observation plus
    recorder fields (diagnostics, policy tokens, model calls)."""

    observation: Observation
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    policy_tokens: int = 0
    model_calls: int = 0


AttemptFn = Callable[[CellMeta, Observation | None, Mapping[str, Any]],
                     Awaitable[Observation | AttemptResult]]


class LiveGrid:
    """The branch × attempt bound of one live episode (``GridPlan`` B, R) plus direction tags."""

    def __init__(self, plan: GridPlan, *, directions: Sequence[Mapping[str, Any]] | None = None,
                 tree_id: str | None = None, parent_node_id: str | None = None) -> None:
        if plan.branch_count < 1:
            raise ValueError(f"branch_count must be >= 1, got {plan.branch_count}")
        if plan.refine_count < 0:
            raise ValueError(f"refine_count must be >= 0, got {plan.refine_count}")
        self.plan = plan
        self.B = int(plan.branch_count)
        self.R = int(plan.refine_count)
        dirs = [dict(d) for d in (directions or ())][: self.B]
        dirs += [{} for _ in range(self.B - len(dirs))]
        self._directions = tuple(dirs)
        self.tree_id = tree_id or uuid.uuid4().hex
        self.parent_node_id = parent_node_id
        self._pos = {cell_id(b, a): (b, a) for b in range(self.B) for a in range(self.R + 1)}

    @property
    def cells(self) -> list[str]:
        """All grid cell ids, branch-major."""
        return list(self._pos)

    def position(self, cid: str) -> tuple[int, int]:
        """(branch, attempt) of a grid cell; KeyError for ids outside the grid."""
        return self._pos[cid]

    def tags(self, branch: int) -> dict[str, Any]:
        """A private copy of the direction tags of ``branch``."""
        return dict(self._directions[branch])


class LiveQuestion:
    """The Question protocol over a :class:`LiveGrid` with real (async) attempts."""

    def __init__(self, grid: LiveGrid, attempt_fn: AttemptFn, *, W: int,
                 baseline_score: float | None = None,
                 loop: asyncio.AbstractEventLoop | None = None,
                 root_multi: bool = True) -> None:
        if W < 1:
            raise ValueError("W must be >= 1")
        self._grid = grid
        self._attempt_fn = attempt_fn
        self._W = int(W)
        self._baseline = baseline_score
        self._loop = loop
        self._root_multi = root_multi
        self._lock = threading.Lock()
        self._revealed: dict[str, Observation] = {}
        self._depth: dict[int, int] = {}
        self._seq_of: dict[str, int] = {}
        self._batch_sizes: list[int] = []
        self._inflight = 0
        self.max_inflight = 0
        root = ReplayNode(node_id=f"{grid.tree_id[:8]}-root", tree_id=grid.tree_id,
                          parent_id=grid.parent_node_id, branch=-1, attempt=-1, created_seq=0,
                          score=baseline_score, evaluated=baseline_score is not None,
                          tags={"role": "explore_root", "B": grid.B, "R": grid.R, "W": self._W})
        self._root = root
        self._nodes: list[ReplayNode] = [root]
        self._node_of: dict[str, str] = {}

    # ------------------------------------------------------------ protocol --

    @property
    def baseline_score(self) -> float | None:
        return self._baseline

    @property
    def max_parallelism(self) -> int:
        return self._W

    @property
    def best_so_far(self) -> float | None:
        scores = [o.score for o in self._revealed.values() if o.score is not None]
        return max(scores) if scores else None

    @property
    def budget_spent(self) -> int:
        return len(self._revealed)

    def reset(self) -> None:
        """No-op before the first probe; afterwards attempts cannot be undone."""
        if self._revealed:
            raise RuntimeError("reset() after probing would discard real attempts; "
                               "start a new LiveQuestion")

    def observed(self) -> dict[str, Observation]:
        return dict(self._revealed)

    def opened_branches(self) -> list[int]:
        return sorted(self._depth)

    def legal_roots(self) -> list[str]:
        return [cell_id(b, 0) for b in range(self._grid.B) if b not in self._depth]

    def legal_actions(self) -> list[str]:
        frontiers = [cell_id(b, d) for b, d in sorted(self._depth.items()) if d <= self._grid.R]
        return self.legal_roots() + frontiers

    def meta(self, cid: str) -> CellMeta:
        b, a = self._grid.position(cid)
        if cid not in self._revealed and cid not in self.legal_actions():
            raise KeyError(f"meta is available for revealed or legal cells only: {cid!r}")
        return CellMeta(branch=b, attempt=a, parent_id=cell_id(b, a - 1) if a > 0 else None,
                        seq=self._seq_of.get(cid, -1), tags=self._grid.tags(b))

    def probe_batch(self, cells: Sequence[str],
                    on_reveal: Callable[[Observation], Any] | None = None) -> list[Observation]:
        """Run one batch of attempts (blocking the calling thread) and reveal them in batch order."""
        batch = self._validate(cells)
        if not batch:
            return []
        if self._loop is not None:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self._loop:
                raise RuntimeError("probe_batch blocks: call it from a worker thread "
                                   "(see solve_live), or await aprobe_batch on the loop")
            results = asyncio.run_coroutine_threadsafe(self._run(batch), self._loop).result()
        else:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                results = asyncio.run(self._run(batch))
            else:
                raise RuntimeError("probe_batch inside a running event loop: bind the loop "
                                   "(solve_live) or use aprobe_batch")
        return self._commit(batch, results, on_reveal)

    async def aprobe_batch(self, cells: Sequence[str],
                           on_reveal: Callable[[Observation], Any] | None = None) -> list[Observation]:
        """Async variant of :meth:`probe_batch` for hosts that drive a policy on the loop."""
        batch = self._validate(cells)
        if not batch:
            return []
        return self._commit(batch, await self._run(batch), on_reveal)

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Schedule future attempts on ``loop`` (used by :func:`solve_live`)."""
        self._loop = loop

    # ------------------------------------------------------------ recording --

    def nodes(self) -> list[ReplayNode]:
        """The recorded discovery tree: the explore root node plus one node per attempt."""
        return [dataclasses.replace(n, tags=dict(n.tags), diagnostics=dict(n.diagnostics))
                for n in self._nodes]

    @property
    def rounds(self) -> int:
        return len(self._batch_sizes)

    @property
    def batch_sizes(self) -> list[int]:
        return list(self._batch_sizes)

    @property
    def grid(self) -> LiveGrid:
        return self._grid

    # ------------------------------------------------------------- internals --

    def _validate(self, cells: Sequence[str]) -> list[str]:
        if not isinstance(cells, (str, bytes)) and len(cells) == 0:
            return []
        grid = self._grid

        def parent_of(c: str) -> str | None:
            b, a = grid.position(c)
            return cell_id(b, a - 1) if a > 0 else None

        return check_batch(cells, legal=self.legal_actions(), W=self._W, parent_of=parent_of,
                           branch_of=lambda c: grid.position(c)[0], root_multi=self._root_multi)

    async def _run(self, batch: list[str]) -> list[Observation | AttemptResult]:
        sem = asyncio.Semaphore(self._W)

        async def one(cid: str) -> Observation | AttemptResult:
            b, a = self._grid.position(cid)
            parent = self._revealed.get(cell_id(b, a - 1)) if a > 0 else None
            meta = CellMeta(branch=b, attempt=a, parent_id=cell_id(b, a - 1) if a > 0 else None,
                            seq=-1, tags=self._grid.tags(b))
            async with sem:
                with self._lock:
                    self._inflight += 1
                    self.max_inflight = max(self.max_inflight, self._inflight)
                try:
                    return await self._attempt_fn(meta, parent, self._grid.tags(b))
                except Exception as exc:  # noqa: BLE001 — a failed attempt is an observation
                    return Observation(branch=b, attempt=a, score=None, evaluated=False,
                                       valid=None, fail_class="env_failure",
                                       error=f"{type(exc).__name__}: {exc}"[:500],
                                       delta_vs_baseline=None, delta_vs_parent=None)
                finally:
                    with self._lock:
                        self._inflight -= 1

        return list(await asyncio.gather(*(one(c) for c in batch)))

    def _commit(self, batch: list[str], results: list[Observation | AttemptResult],
                on_reveal: Callable[[Observation], Any] | None) -> list[Observation]:
        out: list[Observation] = []
        for cid, res in zip(batch, results):
            extra: AttemptResult | None = res if isinstance(res, AttemptResult) else None
            raw = extra.observation if extra is not None else res
            b, a = self._grid.position(cid)
            if not isinstance(raw, Observation):
                raw = Observation(branch=b, attempt=a, score=None, evaluated=False, valid=None,
                                  fail_class="env_failure",
                                  error=f"attempt_fn returned {type(raw).__name__}",
                                  delta_vs_baseline=None, delta_vs_parent=None)
            parent_score = (self._revealed[cell_id(b, a - 1)].score if a > 0
                            else self._baseline)
            score = None if raw.score is None else float(raw.score)
            obs = dataclasses.replace(
                raw, branch=b, attempt=a, cell_id=cid, score=score,
                delta_vs_baseline=(score - self._baseline
                                   if score is not None and self._baseline is not None else None),
                delta_vs_parent=(score - parent_score
                                 if score is not None and parent_score is not None else None))
            seq = len(self._nodes)
            parent_node = self._node_of.get(cell_id(b, a - 1)) if a > 0 else self._root.node_id
            node = ReplayNode(
                node_id=f"{self._grid.tree_id[:8]}-{cid}", tree_id=self._grid.tree_id,
                parent_id=parent_node, branch=b, attempt=a, created_seq=seq, score=obs.score,
                evaluated=obs.evaluated, valid=obs.valid, fail_class=obs.fail_class,
                error=obs.error, n_valid=obs.n_valid, n_total=obs.n_total,
                tags={"role": "cell", **self._grid.tags(b)},
                diagnostics=dict(extra.diagnostics) if extra is not None else {},
                policy_tokens=int(extra.policy_tokens) if extra is not None else 0,
                model_calls=int(extra.model_calls) if extra is not None else 0)
            self._nodes.append(node)
            self._node_of[cid] = node.node_id
            self._seq_of[cid] = seq
            self._revealed[cid] = obs
            self._depth[b] = a + 1
            out.append(obs)
        self._batch_sizes.append(len(batch))
        if on_reveal is not None:
            for obs in out:
                on_reveal(obs)
        return out


async def solve_live(policy: Any, question: LiveQuestion, budget: int | None = None) -> Any:
    """Run a synchronous policy ``solve`` against a live question from async code.

    Binds the running loop (attempts are scheduled on it) and executes
    ``policy.solve(question, budget)`` in a worker thread.
    """
    question.bind_loop(asyncio.get_running_loop())
    try:
        return await asyncio.to_thread(policy.solve, question, budget)
    finally:
        question.bind_loop(None)


def summarize_live(question: LiveQuestion, *, iteration: int, beta: float,
                   plan: GridPlan | None = None, policy_version: str = "") -> LiveCycleManifest:
    """Build the ``live_cycle_manifest.json`` facts of a finished live episode."""
    grid = question.grid
    plan = plan or grid.plan
    revealed = list(question.observed().values())
    successes = [o for o in revealed if is_success(o) and o.score is not None]
    best = max(successes, key=lambda o: (o.score, -o.attempt), default=None)
    by_branch: dict[int, list[Observation]] = {}
    for o in revealed:
        by_branch.setdefault(o.branch, []).append(o)
    improving = 0
    late = 0
    half = math.ceil(grid.R / 2) if grid.R > 0 else 1
    base = question.baseline_score
    for traj in by_branch.values():
        traj.sort(key=lambda o: o.attempt)
        first = traj[0]
        if first.attempt == 0 and is_success(first) and (first.delta_vs_baseline or 0.0) > 0:
            improving += 1
        ok = [o for o in traj if is_success(o) and o.score is not None]
        if ok:
            top = max(ok, key=lambda o: (o.score, -o.attempt))
            ref = first.score if is_success(first) and first.score is not None else base
            if top.attempt >= half and (ref is None or top.score > ref):
                late += 1
    kinds = [failure_kind(o) for o in revealed]
    depth = {o.branch: 0 for o in revealed}
    for o in revealed:
        depth[o.branch] = max(depth[o.branch], o.attempt + 1)
    return LiveCycleManifest(
        iteration=iteration, beta=float(beta), best_score=best.score if best else None,
        baseline_score=base, planned_branch_count=int(plan.branch_count),
        planned_refine_count=int(plan.refine_count), effective_branch_count=grid.B,
        effective_refine_count=grid.R, opened_width=len(depth),
        max_depth=max(depth.values(), default=0), probes=len(revealed), rounds=question.rounds,
        W=question.max_parallelism, improving_roots=improving, late_gain_branches=late,
        best_attempt=best.attempt if best else None,
        hard_failures=sum(1 for k in kinds if k == "hard"),
        repairable_failures=sum(1 for k in kinds if k == "repairable"),
        policy_version=policy_version, plan_reason=plan.reason)


__all__ = ["AttemptFn", "AttemptResult", "LiveGrid", "LiveQuestion", "solve_live",
           "summarize_live"]
