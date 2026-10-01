"""Frozen value types of the formula library (05 §1 symbol table, 33 §1).

`TaskResult` and `EvalResult` keep the field names of the reference
implementation (google-research/rrsi `rrsi/evaluate.py`, commit be50316) so the
two can be converted field by field in differential tests and so stored
``eval.json`` files are interchangeable.

All containers are copied into immutable tuples (or private dict copies) at
construction; instances are frozen dataclasses.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/evaluate.py``: the
TaskResult/EvalResult field layout), Copyright 2026 The rrsi Authors / Google LLC, Apache License 2.0;
modified by PlateerLab.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

Token = int | float | None
"""c(τ) of one trial: policy tokens, or None when not measured (05 §2.2)."""


# --------------------------------------------------------------------------- RRSI


@dataclass(frozen=True)
class TaskResult:
    """Trials of one task x ∈ D: r(x, τ_x^(j)), w(x, τ_x^(j)), c(τ_x^(j)), j = 1..k.

    05 §2.1: a missing trial is recorded as r = 0 with its default weight, so
    `rewards`/`weights` always cover every expected trial; `missing` counts
    them. Empty `weights` means w = 1 per trial and empty `tokens` means every
    c(τ) is unmeasured, exactly as the reference `__post_init__`.
    """

    rewards: Sequence[float]
    weights: Sequence[float] = ()
    tokens: Sequence[Token] = ()
    missing: int = 0
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        rewards = tuple(self.rewards)
        weights = tuple(self.weights) if len(self.weights) else (1.0,) * len(rewards)
        tokens = tuple(self.tokens) if len(self.tokens) else (None,) * len(rewards)
        if len(weights) != len(rewards):
            raise ValueError(f"weights has {len(weights)} entries for {len(rewards)} trials")
        if len(tokens) != len(rewards):
            raise ValueError(f"tokens has {len(tokens)} entries for {len(rewards)} trials")
        if self.missing < 0:
            raise ValueError("missing must be >= 0")
        object.__setattr__(self, "rewards", rewards)
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "tokens", tokens)
        object.__setattr__(self, "extra", dict(self.extra))

    @property
    def mean(self) -> float:
        """Within-task weighted mean Σ_j w r / Σ_j w (0 when Σ w = 0)."""
        w = sum(self.weights)
        return (sum(r * x for r, x in zip(self.rewards, self.weights)) / w) if w else 0.0

    def to_json(self) -> dict[str, Any]:
        return {"rewards": list(self.rewards), "weights": list(self.weights),
                "tokens": list(self.tokens), "missing": self.missing,
                "extra": dict(self.extra)}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> TaskResult:
        return cls(rewards=d["rewards"], weights=d.get("weights") or (),
                   tokens=d.get("tokens") or (), missing=int(d.get("missing", 0)),
                   extra=d.get("extra") or {})


@dataclass(frozen=True)
class EvalResult:
    """Evaluate(H, D, k): Ŝ(H), Ĉ(H) of Eq.3 plus the bookkeeping of 05 §2.1/2.10.

    `n_expected` = |D|·k and `missing` = Σ missing trials.
    """

    job: str
    k: int
    per_task: Mapping[str, TaskResult]
    S: float
    C: float | None
    n_expected: int
    missing: int
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "per_task", dict(self.per_task))
        object.__setattr__(self, "extra", dict(self.extra))

    def to_json(self) -> dict[str, Any]:
        """Same layout as the reference `EvalResult.to_json`."""
        return {"job": self.job, "k": self.k, "S": self.S, "C": self.C,
                "n_expected": self.n_expected, "missing": self.missing,
                "extra": dict(self.extra),
                "per_task": {t: r.to_json() for t, r in self.per_task.items()}}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> EvalResult:
        per = {str(t): TaskResult.from_json(r) for t, r in d["per_task"].items()}
        return cls(job=d["job"], k=int(d["k"]), per_task=per, S=d["S"], C=d["C"],
                   n_expected=int(d["n_expected"]), missing=int(d["missing"]),
                   extra=d.get("extra") or {})


@dataclass(frozen=True)
class EditRecord:
    """One record of the edit history 𝓛_t (R-Eq10, 05 §3.2).

    (t_i, variant, edit_id, ℓ_i, h_i, ΔS_i, ΔC_i, a_i = accepted, outcome, Ŝ, Ĉ,
    bundle, detail, targets_mode, predicted_affected). Unmeasured candidates
    carry ΔS = ΔC = None. `early_stopped` marks a record written by the exact
    early-stop rule of 33 §4 (ΔS is then the upper bound Ŝ_max − Ŝ_t).
    """

    t: int
    variant: str
    edit_id: str | None
    component: str | None
    hypothesis: str | None
    delta_S: float | None
    delta_C: float | None
    accepted: bool
    outcome: str
    S: float | None = None
    C: float | None = None
    bundle: int = 1
    detail: str = ""
    targets_mode: str | None = None
    predicted_affected: Sequence[str] = ()
    early_stopped: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "predicted_affected", tuple(self.predicted_affected))

    def to_json(self) -> dict[str, Any]:
        """The reference JSONL record keys (`History.append_candidate`) plus `early_stopped`."""
        return {"t": self.t, "variant": self.variant, "edit_id": self.edit_id,
                "component": self.component, "hypothesis": self.hypothesis,
                "targets_mode": self.targets_mode,
                "predicted_affected": list(self.predicted_affected),
                "delta_S": self.delta_S, "delta_C": self.delta_C,
                "accepted": self.accepted, "outcome": self.outcome,
                "S": self.S, "C": self.C, "bundle": self.bundle, "detail": self.detail,
                "early_stopped": self.early_stopped}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> EditRecord:
        """Read a record written by us or by the reference `History` (extra keys ignored)."""
        return cls(t=int(d["t"]), variant=str(d.get("variant") or ""),
                   edit_id=d.get("edit_id"), component=d.get("component"),
                   hypothesis=d.get("hypothesis"), delta_S=d.get("delta_S"),
                   delta_C=d.get("delta_C"), accepted=bool(d.get("accepted")),
                   outcome=str(d.get("outcome") or ""), S=d.get("S"), C=d.get("C"),
                   bundle=int(d.get("bundle") or 0), detail=str(d.get("detail") or ""),
                   targets_mode=d.get("targets_mode"),
                   predicted_affected=tuple(d.get("predicted_affected") or ()),
                   early_stopped=bool(d.get("early_stopped", False)))


@dataclass(frozen=True)
class Candidate:
    """A screened candidate harness H' ∈ 𝓗_t with its declared edits (R-Alg2).

    `ev` is None when the candidate failed a gate before measurement
    (`gate_failure` ∈ {critic_reject, smoke_fail, eval_invalid, no_proposal}).
    """

    variant: str
    edits: Sequence[Mapping[str, Any]] = ()
    ev: EvalResult | None = None
    gate_failure: str | None = None
    detail: str = ""
    commit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "edits", tuple(dict(e) for e in self.edits))

    @property
    def components(self) -> list[str]:
        """comp(H'): the (normalized) component tags of the edits, falsy tags dropped."""
        return [str(c) for e in self.edits if (c := e.get("component"))]


@dataclass(frozen=True)
class Decision:
    """Outcome of Algorithm 2 for one candidate (R-Eq5/7/8/17).

    `reason_code` ∈ {gate code | "not_evaluated", "floor", "cost_rule", "guard",
    "admissible"}; `reason` is the human text (identical to the reference).
    """

    variant: str
    admissible: bool
    reason: str
    reason_code: str
    S: float | None = None
    C: float | None = None
    delta_S: float | None = None
    delta_C: float | None = None
    novelty: int = 0
    guards: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "guards", tuple(self.guards))

    def to_json(self) -> dict[str, Any]:
        return {"variant": self.variant, "admissible": self.admissible,
                "reason": self.reason, "reason_code": self.reason_code, "S": self.S,
                "C": self.C, "delta_S": self.delta_S, "delta_C": self.delta_C,
                "novelty": self.novelty, "guards": list(self.guards)}


@dataclass(frozen=True)
class RRSIParams:
    """Hyperparameters of RRSI (R Appendix, reference `RRSIConfig` defaults).

    T, k, m, b_min, b_max (Eq.4), w, m_draft (Eq.13), δ and z (§2.4), β0, β1
    (Eq.7), w_s, w_c, w_n (Eq.17), n_prune (Eq.11), φ = invalid_missing_frac
    (05 §2.10). `delta=None` means "use the calibrated value".
    """

    T: int = 20
    k: int = 2
    m: int = 2
    b_min: int = 1
    b_max: int = 4
    w: int = 3
    m_draft: int = 1
    delta: float | None = None
    delta_z: float = 2.0
    beta0: float = 0.10
    beta1: float = 40.0
    w_s: float = 100.0
    w_c: float = 15.0
    w_n: float = 0.5
    n_prune: int = 4
    invalid_missing_frac: float = 0.15

    def __post_init__(self) -> None:
        if self.k < 1 or self.m < 1:
            raise ValueError("k and m must be >= 1")
        if not 1 <= self.b_min <= self.b_max:
            raise ValueError("need 1 <= b_min <= b_max")
        if not 0 <= self.m_draft <= self.m:
            raise ValueError("need 0 <= m_draft <= m")
        if self.w < 1 or self.n_prune < 0:
            raise ValueError("need w >= 1 and n_prune >= 0")
        if self.delta is not None and self.delta < 0:
            raise ValueError("delta must be >= 0")
        if min(self.beta0, self.beta1, self.w_s, self.w_c, self.w_n) < 0:
            raise ValueError("beta0, beta1, w_s, w_c, w_n must be >= 0")
        if not 0 <= self.invalid_missing_frac <= 1:
            raise ValueError("invalid_missing_frac must lie in [0, 1]")


@dataclass(frozen=True)
class Calibration:
    """δ = z · sd_null (05 §2.4); the same fields as the reference `calibrate` dict.

    `S_per_eval`/`max_abs_diff` are present only when R ≥ 2.
    """

    delta: float
    z: float
    sd_null: float
    sd_null_bootstrap: float
    se_bootstrap: float
    method: str
    n_evals: int
    k: int
    n_tasks: int
    S_base: float
    C_base: float | None
    S_per_eval: tuple[float, ...] | None = None
    max_abs_diff: float | None = None

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "delta": self.delta, "z": self.z, "sd_null": self.sd_null,
            "sd_null_bootstrap": self.sd_null_bootstrap, "se_bootstrap": self.se_bootstrap,
            "method": self.method, "n_evals": self.n_evals, "k": self.k,
            "n_tasks": self.n_tasks, "S_base": self.S_base, "C_base": self.C_base}
        if self.S_per_eval is not None:
            d["S_per_eval"] = list(self.S_per_eval)
            d["max_abs_diff"] = self.max_abs_diff
        return d


@dataclass(frozen=True)
class AcceptedEdit:
    """An accepted edit (a_i = 1) still in the incumbent, listed with its 𝓑_t entry."""

    t: int
    edit_id: str | None
    hypothesis: str | None
    delta_S: float | None

    def to_json(self) -> dict[str, Any]:
        return {"t": self.t, "edit_id": self.edit_id, "hypothesis": self.hypothesis,
                "delta_S": self.delta_S}


@dataclass(frozen=True)
class PruneTarget:
    """ℓ ∈ 𝓑_t (R-Eq14) with g_t(ℓ) (None for −∞, rounded to 5 places) and its machinery."""

    component: str
    recent_best_gain: float | None
    accepted_edits_in_incumbent: tuple[AcceptedEdit, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {"component": self.component, "recent_best_gain": self.recent_best_gain,
                "accepted_edits_in_incumbent":
                    [e.to_json() for e in self.accepted_edits_in_incumbent]}


@dataclass(frozen=True)
class Exploration:
    """𝓔_t = (σ_t, 𝒰_t, m_draft) of R-Eq13 (proposer text is built by the evolve layer)."""

    sigma: int
    untried: tuple[str, ...]
    m_draft: int

    @property
    def reserved_active(self) -> bool:
        """True when σ_t = 1 ∧ 𝒰_t ≠ ∅, i.e. reserved exploration slots exist this round."""
        return bool(self.sigma and self.untried)


@dataclass(frozen=True)
class AttributionRow:
    """Attribution scoreboard row (05 §6, reference `Run.attribute`): hit_rate and
    unpredicted regressions (per-task mean drop ≥ 1/k outside predicted_affected)."""

    t: int | None
    variant: str | None
    edit_id: str | None
    component: str | None
    hypothesis: str
    n_predicted: int
    predicted_hit: tuple[str, ...]
    hit_rate: float | None
    unpredicted_regressions: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"t": self.t, "variant": self.variant, "edit_id": self.edit_id,
                "component": self.component, "hypothesis": self.hypothesis,
                "n_predicted": self.n_predicted, "predicted_hit": list(self.predicted_hit),
                "hit_rate": self.hit_rate,
                "unpredicted_regressions": list(self.unpredicted_regressions)}


# -------------------------------------------------------------------------- Dream


@dataclass(frozen=True)
class ReplayEpisode:
    """One replay of policy π^m on frozen world 𝒯_i at a fixed β (D §3, D-Eq1, D-B).

    - `batch_sizes`: |C_k| of every non-empty batch, in order.
    - `revealed_scores`: s_v of the revealed non-root nodes, in reveal order.
    - `root_score`: s_r (None when the root has no score).
    - `n_revealed`: N_i^m = |𝒯_i^{m,k★}| − 1.
    - `rounds`: k_i^{m,★}, completed decision rounds.
    - `W`: worker count (max_parallelism); `N_max`: non-root nodes of the world.
    - `curve`: anytime curve ((probes, best-so-far s̃), …).
    """

    world_id: str
    beta: float
    batch_sizes: tuple[int, ...]
    revealed_scores: tuple[float, ...]
    root_score: float | None
    n_revealed: int
    rounds: int
    W: int
    N_max: int
    stop_reason: str = ""
    curve: tuple[tuple[int, float], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "batch_sizes", tuple(self.batch_sizes))
        object.__setattr__(self, "revealed_scores", tuple(self.revealed_scores))
        object.__setattr__(self, "curve", tuple((int(n), float(b)) for n, b in self.curve))
        if self.n_revealed != len(self.revealed_scores):
            raise ValueError("n_revealed must equal len(revealed_scores)")
        if self.rounds < 0 or self.N_max < 0 or self.W < 1:
            raise ValueError("need rounds >= 0, N_max >= 0, W >= 1")
        if any(b < 0 for b in self.batch_sizes):
            raise ValueError("batch sizes must be >= 0")


@dataclass(frozen=True)
class LiveCycle:
    """One live cycle: iteration, final best score and the baked β (D-B §3.4 manifest)."""

    iteration: int
    best_score: float
    beta: float


class SweepPoint(NamedTuple):
    """One β of an offline β sweep: (β, attainment, work, reward)."""

    beta: float
    attainment: float
    work: float
    reward: float


@dataclass(frozen=True)
class BetaSweep:
    """The archived β sweep paired with a live cycle (D-B `beta_sweep.json`)."""

    iteration: int
    points: Sequence[tuple[float, float, float, float]] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(SweepPoint(*p) for p in self.points))


@dataclass(frozen=True)
class BetaDecision:
    """Next default β (05 §4.4) with the branch taken and a factual reason."""

    beta: float
    branch: str
    reason: str


@dataclass(frozen=True)
class GridPlan:
    """GridPlan(branch_count = B, refine_count = R, reason) of D-B `plan_grid`."""

    branch_count: int
    refine_count: int
    reason: str = ""


@dataclass(frozen=True)
class GridPlanningContext:
    """Prefix-safe facts handed to `plan_grid` (D-B §3.5)."""

    hard_max_branch_count: int
    hard_max_refine_count: int
    worker_cap: int
    fallback_branch_count: int
    fallback_refine_count: int
    trace_branch_count: int | None = None
    trace_refine_count: int | None = None
    history: tuple[Any, ...] = ()


@dataclass(frozen=True)
class GridValidity:
    """Result of grid validation (05 §4.5): hard caps, replay support, cell count B·(R+1)."""

    valid: bool
    in_support: bool
    cells: int
    reasons: tuple[str, ...] = ()

    @property
    def rewardable(self) -> bool:
        """A plan earns replay reward only when it is valid and inside the trace support."""
        return self.valid and self.in_support
