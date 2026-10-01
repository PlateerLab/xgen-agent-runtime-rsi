"""The exploration decision interface shared by live runs and replay.

Dream-RSI (arXiv:2609.14858, §3 and Appendix B) uses *one* decision
interface online and offline; only the transition after a chosen batch
differs (a live attempt vs. revealing a recorded child). Everything a policy
may touch is defined here:

- :class:`Observation` / :class:`CellMeta` — what a revealed cell and a legal
  cell look like to a policy (docs/research/04 §2).
- :class:`Question` — the protocol implemented by ``LiveQuestion``
  (``explore.grid``) and ``ReplayQuestion`` (``dream.replay``).
- :class:`LLMDesignedMethod` — base class of every exploration policy π_E
  (``solve`` + ``plan_grid``; one β knob read from ``config``).
- :class:`SimResult`, :func:`_budget_done`, :func:`_record_curve`,
  :func:`finalize_result` — the loop helpers of the policy skeleton.
- :class:`LiveCycleManifest` — the frozen per-cycle summary handed to
  ``plan_grid`` through ``GridPlanningContext.history``.

Batch legality (04 §3.1/§3.3) is enforced by :func:`check_batch`: distinct
cells, all legal *before* the call, at most ``max_parallelism`` cells, several
roots and/or one frontier per opened branch, never a parent together with its
child. An illegal batch raises ``ValueError``.

Every module-level constant is immutable: policy code runs in a sandbox that
re-imports this module by reference, so nothing here may serve as a hidden
channel between episodes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, Protocol, runtime_checkable

from xgen_rsi.rsi_math.types import GridPlan, GridPlanningContext

ROOT = "r"
"""The symbolic root r of the formal model; legal only with ``root_policy="earliest"`` (E1)."""


def cell_id(branch: int, attempt: int) -> str:
    """Canonical cell id ``b{branch}a{attempt}`` used by live grids and replay worlds alike."""
    return f"b{int(branch)}a{int(attempt)}"


@dataclass(frozen=True)
class Observation:
    """The stored outcome of one revealed cell (04 §2).

    ``delta_vs_baseline = score − baseline_score``; ``delta_vs_parent = score −
    parent score`` where the parent of attempt 0 is the root (its score is the
    baseline). Either delta is None when one side has no score. ``cell_id`` is
    an extra convenience field (not in the paper's list).
    """

    branch: int
    attempt: int
    score: float | None
    evaluated: bool
    valid: bool | None
    fail_class: str
    error: str | None
    delta_vs_baseline: float | None
    delta_vs_parent: float | None
    n_valid: int | None = None
    n_total: int | None = None
    cell_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> Observation:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass(frozen=True)
class CellMeta:
    """Structural facts of a legal or revealed cell: never an outcome.

    ``parent_id`` is the parent cell id, None for a cell that opens a branch
    (its parent is the root r). ``seq`` is the creation order. ``tags`` carries
    structure such as the direction assigned to the branch (E14); it is a
    private copy, mutating it changes nothing.
    """

    branch: int
    attempt: int
    parent_id: str | None
    seq: int
    tags: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Question(Protocol):
    """One exploration episode as seen by a policy (04 §2).

    ``best_so_far`` and ``budget_spent`` are bookkeeping only: decisions must
    be derived from :meth:`observed`. ``reset`` is allowed only before the
    first probe of an episode (afterwards it raises, so spent probes cannot be
    discarded).
    """

    def reset(self) -> None: ...

    def observed(self) -> dict[str, Observation]: ...

    def legal_actions(self) -> list[str]: ...

    def legal_roots(self) -> list[str]: ...

    def opened_branches(self) -> list[int]: ...

    def meta(self, cell_id: str) -> CellMeta: ...

    def probe_batch(self, cells: Sequence[str],
                    on_reveal: Callable[[Observation], Any] | None = None) -> list[Observation]: ...

    @property
    def baseline_score(self) -> float | None: ...

    @property
    def max_parallelism(self) -> int: ...

    @property
    def best_so_far(self) -> float | None: ...

    @property
    def budget_spent(self) -> int: ...


# ----------------------------------------------------------------- legality --


def check_batch(cells: Sequence[str], *, legal: Sequence[str], W: int,
                parent_of: Callable[[str], str | None], branch_of: Callable[[str], int],
                root_multi: bool = True) -> list[str]:
    """Validate a batch against the rules of 04 §3.1/§3.3; return it as a list.

    - every cell is a string listed in ``legal`` (computed before the call);
    - cells are distinct, except the symbolic root ``ROOT`` which may repeat
      up to its multiplicity in ``legal`` (E1, earliest mode);
    - ``len(cells) <= W``;
    - at most one frontier per opened branch, roots only for unopened
      branches (one cell per branch overall);
    - no cell is the parent of another cell of the batch.

    Raises ``ValueError`` naming the first violated rule.
    """
    if isinstance(cells, (str, bytes)):
        raise ValueError("batch must be a sequence of cell ids, not a string")
    batch = list(cells)
    if len(batch) > W:
        raise ValueError(f"batch of {len(batch)} cells exceeds max_parallelism {W}")
    for c in batch:
        if not isinstance(c, str):
            raise ValueError(f"cell id {c!r} is not a string")
    legal_count: dict[str, int] = {}
    for c in legal:
        legal_count[c] = legal_count.get(c, 0) + 1
    seen: dict[str, int] = {}
    for c in batch:
        seen[c] = seen.get(c, 0) + 1
        if c not in legal_count:
            raise ValueError(f"cell {c!r} is not legal before this call")
        if c != ROOT and seen[c] > 1:
            raise ValueError(f"duplicate cell {c!r} in batch")
        if c == ROOT and seen[c] > legal_count[c]:
            raise ValueError("root r selected more often than unopened branches allow")
    if not root_multi and seen.get(ROOT, 0) > 1:
        raise ValueError("root r may be selected at most once per batch in this mode")
    concrete = [c for c in batch if c != ROOT]
    branches = [branch_of(c) for c in concrete]
    if len(set(branches)) != len(branches):
        raise ValueError("batch holds more than one cell of the same branch")
    members = set(concrete)
    for c in concrete:
        p = parent_of(c)
        if p is not None and p in members:
            raise ValueError(f"batch holds parent {p!r} together with its child {c!r}")
    return batch


# ------------------------------------------------------------ policy base --


@dataclass
class SimResult:
    """What ``solve`` returns: the policy's own anytime curve and final bookkeeping.

    Evaluators never trust this object: the replay engine keeps its own ledger.
    ``curve`` holds ``(probes, best_so_far)`` pairs recorded by
    :func:`_record_curve`.
    """

    curve: list[tuple[int, float | None]] = field(default_factory=list)
    best: float | None = None
    probes: int = 0
    notes: dict[str, Any] = field(default_factory=dict)


def _budget_done(question: Question, budget: int | None = None) -> bool:
    """True when an optional probe budget is spent or no legal action remains.

    Replay calls ``solve(question, budget=None)``; the question then ends the
    episode by offering no legal action (all revealed or the round limit K2).
    """
    if budget is not None and question.budget_spent >= budget:
        return True
    return not question.legal_actions()


def _record_curve(res: SimResult, question: Question) -> None:
    """``on_reveal`` hook of the skeleton: append ``(probes, best_so_far)`` (bookkeeping)."""
    res.curve.append((question.budget_spent, question.best_so_far))


def finalize_result(question: Question, res: SimResult) -> SimResult:
    """Copy the final bookkeeping (best score, probes) into ``res`` and return it."""
    res.best = question.best_so_far
    res.probes = question.budget_spent
    return res


class LLMDesignedMethod:
    """Base class of an exploration policy π_E (04 §2, §3.4–3.5).

    ``config`` carries exactly one knob, ``beta`` ∈ [0, 1], fixed for the whole
    episode; a subclass bakes its default by setting ``DEFAULT_BETA`` or by
    reading ``self.config.get("beta", <default>)`` in its own ``__init__``.
    ``plan_grid`` here returns an explicit conservative bootstrap plan; every
    developed policy must override it (D-B).
    """

    NAME = "LLMDesignedMethod"
    DEFAULT_BETA = 0.6

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.beta = float(self.config.get("beta", self.DEFAULT_BETA))

    def solve(self, question: Question, budget: int | None = None) -> SimResult:
        raise NotImplementedError

    def plan_grid(self, context: GridPlanningContext) -> GridPlan:
        """Conservative bootstrap: the context's fallback grid clamped to the hard caps
        and, in replay, to the frozen trace's support."""
        return bootstrap_plan(context, "bootstrap: base class plan_grid (no history used)")


def bootstrap_plan(context: GridPlanningContext, reason: str) -> GridPlan:
    """Fallback grid of ``context`` clamped to its hard caps and trace support (05 §4.5)."""
    B = max(1, min(int(context.fallback_branch_count), int(context.hard_max_branch_count)))
    R = max(0, min(int(context.fallback_refine_count), int(context.hard_max_refine_count)))
    if context.trace_branch_count is not None:
        B = max(1, min(B, int(context.trace_branch_count)))
    if context.trace_refine_count is not None:
        R = max(0, min(R, int(context.trace_refine_count)))
    return GridPlan(branch_count=B, refine_count=R, reason=reason)


# --------------------------------------------------------------- manifests --


@dataclass(frozen=True)
class LiveCycleManifest:
    """Summary of one completed live exploration cycle (``live_cycle_manifest.json``).

    Prefix-safe facts for ``plan_grid`` and the cross-cycle β rule (04 §3.4–3.5):
    the planned and effective grid, opened width, deepest attempt count, probe
    work, decision rounds, final best and baseline score, the baked β, and a
    few derived counts:

    - ``improving_roots``: opened branches whose attempt 0 succeeded above baseline;
    - ``late_gain_branches``: branches whose best success came in the second half
      of the refine depth and beat their own attempt 0;
    - ``best_attempt``: attempt index of the overall best success;
    - ``hard_failures`` / ``repairable_failures``: failure counts by class.
    """

    iteration: int
    beta: float
    best_score: float | None
    baseline_score: float | None = None
    planned_branch_count: int = 0
    planned_refine_count: int = 0
    effective_branch_count: int = 0
    effective_refine_count: int = 0
    opened_width: int = 0
    max_depth: int = 0
    probes: int = 0
    rounds: int = 0
    W: int = 1
    improving_roots: int = 0
    late_gain_branches: int = 0
    best_attempt: int | None = None
    hard_failures: int = 0
    repairable_failures: int = 0
    policy_version: str = ""
    plan_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> LiveCycleManifest:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


__all__ = (
    "ROOT", "CellMeta", "GridPlan", "GridPlanningContext", "LLMDesignedMethod",
    "LiveCycleManifest", "Observation", "Question", "SimResult", "_budget_done",
    "_record_curve", "bootstrap_plan", "cell_id", "check_batch", "finalize_result",
)
