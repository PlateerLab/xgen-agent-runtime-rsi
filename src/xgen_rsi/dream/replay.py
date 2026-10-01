"""Replay simulator: a frozen world behind the shared Question interface (03 §3.4, 04 §1, §7).

``ReplayQuestion(world, W, root_policy=...)`` implements the Question
protocol over a :class:`~xgen_rsi.dream.world.World`. Instead of generating a
new attempt, a selected cell deterministically reveals its *recorded* child
(``Child(v; 𝒯_i, 𝒯_i^{m,k})``):

- a frontier cell (the next attempt of an opened branch) reveals that cell —
  the unique recorded child of the branch's current leaf;
- ``root_policy="choose"`` (Appendix B, xgen default): a legal root is the
  attempt-0 cell of a specific unopened branch;
- ``root_policy="earliest"`` (paper Sec 3, E1): the symbolic root
  :data:`~xgen_rsi.explore.api.ROOT` (``"r"``) opens the unopened branch with
  the earliest creation ``seq``; listed (and selectable) once per unopened
  branch when ``root_multi`` is true, otherwise once.

Legal cells are exactly the recorded continuations: a leaf without a
recorded child is not offered (its Child is ∅ and selecting it is illegal),
cells outside the effective grid (a ``GridPlan`` sub-grid) are out of
support and never legal.

Stop conditions (E1–E3): the policy selects an empty batch; the round limit
K2 is reached (default ``|𝒯_i| − 1`` of the effective grid); every recorded
node is revealed. At K2 or full reveal the question offers no legal action,
so the policy's loop ends.

Information hiding: the policy object receives only the facade. Its single
slot holds closures; the world, the ledger and unrevealed observations are
not reachable through attributes (the sandbox additionally rejects dunder
and private attribute access). ``meta()`` answers only for revealed or
currently legal cells, and the ``seq`` of an unrevealed cell reads −1. Spent
probes cannot be undone: ``reset()`` after the first probe raises.

:func:`run_episode` runs one (policy, world, β) episode and returns a
:class:`ReplayRun`: the ``rsi_math.ReplayEpisode`` (Eq.1 / evaluator input)
plus a per-round trace (prefix summary, batch, revealed outcomes) for
``policy_execution_traces.jsonl``.
"""

from __future__ import annotations

import math
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from xgen_rsi.explore.api import (
    ROOT,
    CellMeta,
    GridPlan,
    LiveCycleManifest,
    Observation,
    check_batch,
)
from xgen_rsi.explore.signals import is_success
from xgen_rsi.rsi_math import ReplayEpisode, validate_grid
from xgen_rsi.rsi_math.modes import RootPolicy
from xgen_rsi.rsi_math.types import GridValidity

from .sandbox import (
    LoadedPolicy,
    PolicyTimeout,
    can_isolate,
    check_deadline,
    deadline_after,
    run_isolated,
    run_limited,
)
from .world import World, WorldCell, thaw

STOP_EMPTY = "empty_batch"
STOP_K2 = "k2"
STOP_ALL = "all_revealed"
STOP_OUT_OF_SUPPORT = "out_of_support"
STOP_INVALID_PLAN = "invalid_plan"
STOP_TIMEOUT = "timeout"
STOP_ERROR = "error"


class _Engine:
    """Host-side state of one replay episode (never handed to the policy)."""

    def __init__(self, world: World, W: int, root_policy: RootPolicy, root_multi: bool,
                 K2: int | None, grid: tuple[int, int] | None, deadline: float | None) -> None:
        if W < 1:
            raise ValueError("W must be >= 1")
        if root_policy not in ("choose", "earliest"):
            raise ValueError(f"unknown root_policy {root_policy!r}")
        self.world = world
        self.W = int(W)
        self.root_policy = root_policy
        self.root_multi = bool(root_multi)
        self.deadline = deadline
        chains: dict[int, tuple[WorldCell, ...]] = {}
        for b, chain in world.branches.items():
            if grid is not None:
                B, R = grid
                if b >= B:
                    continue
                chain = tuple(c for c in chain if c.attempt <= R)
            if chain:
                chains[b] = chain
        self.chains = chains
        self.n_eff = sum(len(c) for c in chains.values())
        self.K2 = self.n_eff if K2 is None else int(K2)
        self.root_norm = world.normalizer("xgen")
        self.floor = world.floor_score
        self.revealed: dict[str, Observation] = {}
        self.order: list[WorldCell] = []
        self.depth: dict[int, int] = {}
        self.batch_sizes: list[int] = []
        self.trace: list[dict[str, Any]] = []
        self.curve: list[tuple[int, float]] = []
        self.best_q: float | None = (self.root_norm(world.root_score)
                                     if world.root_score is not None else None)

    # ----------------------------------------------------------- queries --

    def exhausted(self) -> bool:
        return len(self.batch_sizes) >= self.K2 or len(self.revealed) >= self.n_eff

    def _unopened(self) -> list[int]:
        return [b for b in self.chains if b not in self.depth]

    def legal_roots(self) -> list[str]:
        check_deadline(self.deadline)
        if self.exhausted():
            return []
        unopened = self._unopened()
        if self.root_policy == "earliest":
            n = len(unopened) if self.root_multi else min(1, len(unopened))
            return [ROOT] * n
        return [self.chains[b][0].cell_id for b in sorted(unopened)]

    def frontiers(self) -> list[str]:
        out = []
        for b, d in sorted(self.depth.items()):
            if d < len(self.chains[b]):
                out.append(self.chains[b][d].cell_id)
        return out

    def legal_actions(self) -> list[str]:
        check_deadline(self.deadline)
        if self.exhausted():
            return []
        return self.legal_roots() + self.frontiers()

    def observed(self) -> dict[str, Observation]:
        check_deadline(self.deadline)
        return dict(self.revealed)

    def opened_branches(self) -> list[int]:
        check_deadline(self.deadline)
        return sorted(self.depth)

    def meta(self, cid: str) -> CellMeta:
        check_deadline(self.deadline)
        if cid == ROOT and self.root_policy == "earliest":
            return CellMeta(branch=-1, attempt=-1, parent_id=None, seq=0, tags={"role": "root"})
        if cid in self.revealed:
            c = self.world.cell(cid)
            return CellMeta(branch=c.branch, attempt=c.attempt, parent_id=c.parent_id,
                            seq=c.seq, tags=thaw(c.tags))
        if isinstance(cid, str) and cid in self.legal_actions():
            c = self.world.cell(cid)
            return CellMeta(branch=c.branch, attempt=c.attempt, parent_id=c.parent_id,
                            seq=-1, tags=thaw(c.tags))
        raise KeyError(f"meta is available for revealed or legal cells only: {cid!r}")

    def best_so_far(self) -> float | None:
        scores = [o.score for o in self.revealed.values() if o.score is not None]
        return max(scores) if scores else None

    # --------------------------------------------------------- transition --

    def reset(self) -> None:
        check_deadline(self.deadline)
        if self.revealed:
            raise RuntimeError("reset() after probing would discard spent probes; "
                               "a replay episode cannot be restarted")

    def probe(self, cells: Sequence[str],
              on_reveal: Callable[[Observation], Any] | None) -> list[Observation]:
        check_deadline(self.deadline)
        if not isinstance(cells, (str, bytes)) and len(cells) == 0:
            return []
        if self.exhausted():
            raise ValueError("episode is over (all revealed or round limit K2 reached)")
        legal = self.legal_actions()
        world = self.world

        def parent_of(c: str) -> str | None:
            return world.cell(c).parent_id

        batch = check_batch(cells, legal=legal, W=self.W, parent_of=parent_of,
                            branch_of=lambda c: world.cell(c).branch,
                            root_multi=self.root_multi)
        if self.root_policy == "choose" and not self.root_multi:
            n_roots = sum(1 for c in batch if world.cell(c).attempt == 0)
            if n_roots > 1:
                raise ValueError("at most one root may be opened per batch in this mode")
        earliest = sorted(self._unopened(), key=lambda b: self.chains[b][0].seq)
        resolved: list[WorldCell] = []
        for c in batch:
            if c == ROOT:
                resolved.append(self.chains[earliest.pop(0)][0])
            else:
                resolved.append(world.cell(c))
        prefix = self._prefix_summary(len(legal))
        out: list[Observation] = []
        for cell in resolved:
            obs = cell.obs
            self.revealed[cell.cell_id] = obs
            self.order.append(cell)
            self.depth[cell.branch] = cell.attempt + 1
            score = obs.score if obs.score is not None else self.floor
            q = self.root_norm(score)
            self.best_q = q if self.best_q is None else max(self.best_q, q)
            self.curve.append((len(self.revealed), self.best_q))
            out.append(obs)
        self.batch_sizes.append(len(batch))
        self.trace.append({
            "round": len(self.batch_sizes), "prefix": prefix, "batch": list(batch),
            "resolved": [c.cell_id for c in resolved],
            "revealed": [_obs_brief(o) for o in out]})
        if on_reveal is not None:
            for obs in out:
                on_reveal(obs)
        return out

    def _prefix_summary(self, n_legal: int) -> dict[str, Any]:
        branches: dict[str, Any] = {}
        for b, d in sorted(self.depth.items()):
            traj = [self.revealed[c.cell_id] for c in self.chains[b][:d]]
            ok = [o.score for o in traj if is_success(o) and o.score is not None]
            branches[str(b)] = {"depth": d, "anchor": max(ok) if ok else None,
                                "last_fail_class": traj[-1].fail_class,
                                "remaining_legal": d < len(self.chains[b])}
        ok_all = [o.score for o in self.revealed.values() if is_success(o) and o.score is not None]
        return {"n_revealed": len(self.revealed), "n_legal": n_legal,
                "unopened": len(self._unopened()),
                "best_success": max(ok_all) if ok_all else None, "branches": branches}


def _obs_brief(o: Observation) -> dict[str, Any]:
    return {"cell_id": o.cell_id, "branch": o.branch, "attempt": o.attempt, "score": o.score,
            "evaluated": o.evaluated, "fail_class": o.fail_class,
            "error": (o.error or "")[:200] or None, "delta_vs_parent": o.delta_vs_parent,
            "delta_vs_baseline": o.delta_vs_baseline}


_ENGINES: weakref.WeakKeyDictionary[Any, _Engine] = weakref.WeakKeyDictionary()


def _bind(engine: _Engine) -> tuple[Callable[..., Any], ...]:
    """Closures over ``engine``: the only state the facade holds."""

    def reset() -> None:
        engine.reset()

    def observed() -> dict[str, Observation]:
        return engine.observed()

    def legal_actions() -> list[str]:
        return engine.legal_actions()

    def legal_roots() -> list[str]:
        return engine.legal_roots()

    def opened_branches() -> list[int]:
        return engine.opened_branches()

    def meta(cid: str) -> CellMeta:
        return engine.meta(cid)

    def probe_batch(cells: Sequence[str],
                    on_reveal: Callable[[Observation], Any] | None = None) -> list[Observation]:
        return engine.probe(cells, on_reveal)

    def baseline() -> float | None:
        return engine.world.baseline_score

    def parallelism() -> int:
        return engine.W

    def best() -> float | None:
        return engine.best_so_far()

    def spent() -> int:
        return len(engine.revealed)

    return (reset, observed, legal_actions, legal_roots, opened_branches, meta, probe_batch,
            baseline, parallelism, best, spent)


class ReplayQuestion:
    """The Question protocol over a frozen world (see the module docstring)."""

    __slots__ = ("_fns", "__weakref__")

    def __init__(self, world: World, W: int, root_policy: RootPolicy = "choose", *,
                 root_multi: bool = True, K2: int | None = None,
                 grid: GridPlan | tuple[int, int] | None = None,
                 deadline: float | None = None) -> None:
        bound = None
        if grid is not None:
            bound = ((int(grid.branch_count), int(grid.refine_count))
                     if isinstance(grid, GridPlan) else (int(grid[0]), int(grid[1])))
        engine = _Engine(world, W, root_policy, root_multi, K2, bound, deadline)
        object.__setattr__(self, "_fns", _bind(engine))
        _ENGINES[self] = engine

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ReplayQuestion is read-only")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ReplayQuestion is read-only")

    def __repr__(self) -> str:
        return f"<ReplayQuestion W={self._fns[8]()} revealed={self._fns[10]()}>"

    def reset(self) -> None:
        self._fns[0]()

    def observed(self) -> dict[str, Observation]:
        return self._fns[1]()

    def legal_actions(self) -> list[str]:
        return self._fns[2]()

    def legal_roots(self) -> list[str]:
        return self._fns[3]()

    def opened_branches(self) -> list[int]:
        return self._fns[4]()

    def meta(self, cell_id: str) -> CellMeta:
        return self._fns[5](cell_id)

    def probe_batch(self, cells: Sequence[str],
                    on_reveal: Callable[[Observation], Any] | None = None) -> list[Observation]:
        return self._fns[6](cells, on_reveal)

    @property
    def baseline_score(self) -> float | None:
        return self._fns[7]()

    @property
    def max_parallelism(self) -> int:
        return self._fns[8]()

    @property
    def best_so_far(self) -> float | None:
        """Bookkeeping only (max revealed score); policies must not decide on it."""
        return self._fns[9]()

    @property
    def budget_spent(self) -> int:
        """Bookkeeping only (probes so far); policies must not decide on it."""
        return self._fns[10]()


def replay_state(question: ReplayQuestion) -> dict[str, Any]:
    """Host-side ledger of a replay question: reveal order, batch sizes, rounds, stop state."""
    e = _ENGINES[question]
    return {"revealed": [c.cell_id for c in e.order], "batch_sizes": list(e.batch_sizes),
            "rounds": len(e.batch_sizes), "n_revealed": len(e.revealed), "K2": e.K2,
            "n_effective": e.n_eff, "exhausted": e.exhausted(), "curve": list(e.curve)}


# ------------------------------------------------------------- episodes --


@dataclass(frozen=True)
class ReplayRun:
    """One replay episode: the Eq.1/evaluator record plus its per-round trace."""

    episode: ReplayEpisode
    rounds: tuple[Mapping[str, Any], ...] = ()
    beta: float | None = None
    plan: GridPlan | None = None
    validity: GridValidity | None = None
    error: str | None = None
    revealed: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_trace_json(self, **extra: Any) -> dict[str, Any]:
        ep = self.episode
        d: dict[str, Any] = {
            "world_id": ep.world_id, "beta": self.beta, "W": ep.W, "stop_reason": ep.stop_reason,
            "probes": ep.n_revealed, "rounds": ep.rounds, "N_max": ep.N_max,
            "batch_sizes": list(ep.batch_sizes), "error": self.error,
            "plan": (None if self.plan is None else
                     {"branch_count": self.plan.branch_count,
                      "refine_count": self.plan.refine_count, "reason": self.plan.reason}),
            "in_support": None if self.validity is None else self.validity.rewardable,
            "decision_rounds": [dict(r) for r in self.rounds]}
        d.update(extra)
        return d


def _coerce_plan(plan: Any) -> GridPlan | None:
    if isinstance(plan, GridPlan):
        return plan
    try:
        return GridPlan(int(plan.branch_count), int(plan.refine_count), str(plan.reason or ""))
    except (AttributeError, TypeError, ValueError):
        return None


def run_episode(policy: Any, world: World, beta: float | None, W: int, *,
                root_policy: RootPolicy = "choose", root_multi: bool = True,
                K2: int | None = None, timeout: float | None | str = "default",
                apply_plan: bool = False, hard_max_branch_count: int | None = None,
                hard_max_refine_count: int | None = None,
                history: Sequence[LiveCycleManifest] = (),
                isolate: bool | None = None) -> ReplayRun:
    """Replay ``policy`` on ``world`` at β (None = the policy's baked default) with W workers.

    Untrusted policy code (a :class:`LoadedPolicy` with a time limit) runs in a **forked child process**
    with CPU-time and address-space limits (``isolate`` default): a single long C call or a memory bomb
    cannot stall or kill the evaluator — the parent kills the child at the deadline and records a timeout.
    """
    kw: dict[str, Any] = dict(root_policy=root_policy, root_multi=root_multi, K2=K2, timeout=timeout,
                              apply_plan=apply_plan, hard_max_branch_count=hard_max_branch_count,
                              hard_max_refine_count=hard_max_refine_count, history=history)
    if isolate is None:
        isolate = isinstance(policy, LoadedPolicy) and policy.timeout is not None and can_isolate()
    if isolate:
        limit = policy.timeout if timeout == "default" else timeout
        return _run_episode_isolated(policy, world, beta, W, kw,
                                     float(limit) if isinstance(limit, (int, float)) else None)
    return _run_episode_inproc(policy, world, beta, W, **kw)


def _run_episode_isolated(policy: Any, world: World, beta: float | None, W: int, kw: dict[str, Any],
                          limit: float | None) -> ReplayRun:
    """The whole episode in a forked, resource-limited child (:func:`sandbox.run_isolated`)."""
    try:
        run = run_isolated(lambda: _run_episode_inproc(policy, world, beta, W, **kw), timeout=limit)
        if isinstance(run, ReplayRun):
            return run
        reason = f"policy process returned {type(run).__name__}"
    except PolicyTimeout as exc:
        reason = f"timeout: {exc}" if "killed" in str(exc) else str(exc)
    except Exception as exc:  # noqa: BLE001 — a failing policy is a recorded outcome
        reason = f"{type(exc).__name__}: {exc}"[:500]
    stop = STOP_TIMEOUT if reason.startswith("timeout") else STOP_ERROR
    ep = ReplayEpisode(world_id=world.world_id, beta=_finite(beta), batch_sizes=(), revealed_scores=(),
                       root_score=world.root_score, n_revealed=0, rounds=0, W=W, N_max=world.N_max,
                       stop_reason=stop, curve=())
    return ReplayRun(episode=ep, beta=beta, error=reason)


def _run_episode_inproc(policy: Any, world: World, beta: float | None, W: int, *,
                        root_policy: RootPolicy = "choose", root_multi: bool = True,
                        K2: int | None = None, timeout: float | None | str = "default",
                        apply_plan: bool = False, hard_max_branch_count: int | None = None,
                        hard_max_refine_count: int | None = None,
                        history: Sequence[LiveCycleManifest] = ()) -> ReplayRun:
    """The episode itself (in this process): see :func:`run_episode`.

    ``policy`` is a :class:`~xgen_rsi.dream.sandbox.LoadedPolicy` (untrusted
    code: fresh namespace, wall-clock limit, integrity check) or a trusted
    callable ``policy(config) -> instance`` such as a policy class. The
    instance is built with ``config = {"beta": β}`` (``{}`` for β = None).

    With ``apply_plan`` the policy's ``plan_grid`` is asked first with the
    world's replay context; an invalid or out-of-support plan earns no
    reward (empty episode, stop reason ``invalid_plan``/``out_of_support``),
    otherwise the replay is restricted to the planned sub-grid.

    Errors and timeouts never raise: the run carries ``error`` and the
    partial episode (probes already spent stay spent).
    """
    config: dict[str, Any] = {} if beta is None else {"beta": float(beta)}
    holder: dict[str, Any] = {}

    def body(inst: Any) -> None:
        holder["inst"] = inst
        bound = None
        if apply_plan:
            ctx = world.planning_context(W=W, hard_max_branch_count=hard_max_branch_count,
                                         hard_max_refine_count=hard_max_refine_count,
                                         history=history)
            plan = _coerce_plan(inst.plan_grid(ctx))
            holder["plan"] = plan
            if plan is None:
                holder["stop"] = STOP_INVALID_PLAN
                return
            validity = validate_grid(plan, ctx, replay=True)
            holder["validity"] = validity
            if not validity.valid:
                holder["stop"] = STOP_INVALID_PLAN
                return
            if not validity.in_support:
                holder["stop"] = STOP_OUT_OF_SUPPORT
                return
            bound = (plan.branch_count, plan.refine_count)
        q = ReplayQuestion(world, W, root_policy, root_multi=root_multi, K2=K2, grid=bound,
                           deadline=holder.get("deadline"))
        holder["engine"] = _ENGINES[q]
        inst.solve(q, None)

    error: str | None = None
    if isinstance(policy, LoadedPolicy):
        limit = policy.timeout if timeout == "default" else timeout
        holder["deadline"] = deadline_after(limit if isinstance(limit, (int, float)) else None)
        try:
            policy.run(body, config, timeout=limit, isolate=False)
        except PolicyTimeout as exc:
            error = f"timeout: {exc}"
        except Exception as exc:  # noqa: BLE001 — a failing policy is a recorded outcome
            error = f"{type(exc).__name__}: {exc}"[:500]
    else:
        limit = None if timeout == "default" else timeout
        holder["deadline"] = deadline_after(limit if isinstance(limit, (int, float)) else None)
        try:
            run_limited(lambda: body(policy(config)), timeout=limit)
        except PolicyTimeout as exc:
            error = f"timeout: {exc}"
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"[:500]

    inst = holder.get("inst")
    eff_beta = beta
    b = getattr(inst, "beta", None) if inst is not None else None
    if isinstance(b, (int, float)) and not isinstance(b, bool) and math.isfinite(b):
        eff_beta = float(b)
    engine: _Engine | None = holder.get("engine")
    if engine is None:
        stop = holder.get("stop") or STOP_ERROR
        if error is not None:
            stop = STOP_TIMEOUT if error.startswith("timeout") else STOP_ERROR
        ep = ReplayEpisode(world_id=world.world_id, beta=_finite(eff_beta), batch_sizes=(),
                           revealed_scores=(), root_score=world.root_score, n_revealed=0,
                           rounds=0, W=W, N_max=world.N_max, stop_reason=stop, curve=())
        return ReplayRun(episode=ep, beta=eff_beta, plan=holder.get("plan"),
                         validity=holder.get("validity"), error=error)
    if error is not None:
        stop = STOP_TIMEOUT if error.startswith("timeout") else STOP_ERROR
    elif len(engine.revealed) >= engine.n_eff:
        stop = STOP_ALL
    elif len(engine.batch_sizes) >= engine.K2:
        stop = STOP_K2
    else:
        stop = STOP_EMPTY
    scores = tuple(c.obs.score if c.obs.score is not None else engine.floor for c in engine.order)
    ep = ReplayEpisode(world_id=world.world_id, beta=_finite(eff_beta),
                       batch_sizes=tuple(engine.batch_sizes), revealed_scores=scores,
                       root_score=world.root_score, n_revealed=len(scores),
                       rounds=len(engine.batch_sizes), W=W, N_max=world.N_max,
                       stop_reason=stop, curve=tuple(engine.curve))
    return ReplayRun(episode=ep, rounds=tuple(engine.trace), beta=eff_beta,
                     plan=holder.get("plan"), validity=holder.get("validity"), error=error,
                     revealed=tuple(c.cell_id for c in engine.order))


def _finite(b: float | None) -> float:
    return float(b) if isinstance(b, (int, float)) and math.isfinite(b) else float("nan")


__all__ = ["ReplayQuestion", "ReplayRun", "STOP_ALL", "STOP_EMPTY", "STOP_ERROR", "STOP_INVALID_PLAN",
           "STOP_K2", "STOP_OUT_OF_SUPPORT", "STOP_TIMEOUT", "replay_state", "run_episode"]
