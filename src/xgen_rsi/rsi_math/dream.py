"""Dream-RSI formulas: replay score (D-Eq1), policy selection, the Appendix B
evaluator (parallel_penalty, pareto.auc v1, pareto.reward), the anytime AUC,
the cross-cycle β rule and grid validation.

Canonical definitions: docs/research/05-formula-reference.md §4,
docs/research/04-dream-rsi-replay-policy.md §5–6 (decisions E1–E15),
docs/design/33-formula-to-code.md §5. Symbols: β_cost = D-Eq1 β1,
β_par = D-Eq1 β2, β (beta_explore) = the policy knob; none of them is RRSI β0/β1.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from .modes import Mode
from .types import (
    BetaDecision,
    BetaSweep,
    GridPlan,
    GridPlanningContext,
    GridValidity,
    LiveCycle,
    ReplayEpisode,
    SweepPoint,
)

Normalizer = Callable[[float], float]

DEFAULT_BETA = 0.6
"""The moderately exploratory default β of 05 §4.4 (insufficient or conflicting evidence)."""


# --------------------------------------------------------------- normalization --


def _identity(s: float) -> float:
    """Paper-mode s̃_v = s_v (E8 replacement value: raw scores)."""
    return s


def make_normalizer(scores_in_world: Sequence[float], baseline: float,
                    mode: Mode = "xgen") -> Normalizer:
    """s̃_v of decision E8 (05 §4.2) for one world 𝒯_i.

    xgen: s̃ = clip((s − s_base)/(s_ceil − s_base), 0, 1) with
    s_ceil = max_{v ∈ 𝒯_i} s_v; when s_ceil − s_base ≤ 0 (or the world has no
    scores) s̃ = 𝟙[s ≥ s_base]. paper: identity (raw s_v).
    """
    if mode == "paper":
        return _identity
    if mode != "xgen":
        raise ValueError(f"unknown mode {mode!r}")
    ceil = max(scores_in_world) if len(scores_in_world) else baseline
    den = ceil - baseline
    if not den > 0:
        def indicator(s: float) -> float:
            """E8 degenerate case: s̃ = 𝟙[s ≥ s_base]."""
            return 1.0 if s >= baseline else 0.0
        return indicator

    def scaled(s: float) -> float:
        """E8: s̃ = clip((s − s_base)/(s_ceil − s_base), 0, 1)."""
        return min(1.0, max(0.0, (s - baseline) / den))
    return scaled


# ------------------------------------------------------------------ D-Eq.1 score --


def replay_value(ep: ReplayEpisode, *, beta_cost: float, beta_par: float,
                 norm: Normalizer) -> float:
    """V_i^m = max_{v ∈ 𝒯_i^{m,k★}} s̃_v − β1·N_i^m + β2·N_i^m / max{1, k_i^{m,★}} (D-Eq1).

    The max ranges over the revealed nodes and the root. An empty replay
    (N = 0, k★ = 0) scores s̃_r, or 0.0 when the root has no score (E15).
    """
    vals = [norm(s) for s in ep.revealed_scores]
    if ep.root_score is not None:
        vals.append(norm(ep.root_score))
    best = max(vals) if vals else 0.0
    N = ep.n_revealed
    return best - beta_cost * N + beta_par * N / max(1, ep.rounds)


def mean_value(values: Sequence[float]) -> float:
    """V^m = (1/t) Σ_{i=1..t} V_i^m (D-Eq1); t ≥ 1 required."""
    if not values:
        raise ValueError("V^m needs at least one world")
    return math.fsum(values) / len(values)


def select_policy(V: Sequence[float], *, tie: Mode = "xgen", tol: float = 0.0) -> int:
    """m★ = argmax_{m ∈ {0..M−1}} V^m (05 §4.2), so that V^{m★} ≥ V^0.

    paper: the first maximum (Python ``max``). xgen (decision E11): among the
    versions within `tol` of the maximum, π^0 first, then the smallest m.
    With ``tol=0`` the two rules coincide, because the first exact maximum is
    already index 0 whenever V^0 is a maximum.
    """
    if not V:
        raise ValueError("no policy versions")
    if any(math.isnan(v) for v in V):
        raise ValueError("V contains NaN")
    if tie == "paper":
        return max(range(len(V)), key=V.__getitem__)
    if tie != "xgen":
        raise ValueError(f"unknown tie mode {tie!r}")
    if tol < 0:
        raise ValueError("tol must be >= 0")
    top = max(V) - tol
    return next(m for m, v in enumerate(V) if v >= top)


def assert_non_decreasing(V: Sequence[float], m_star: int) -> None:
    """Check the selection guarantee V^{m★} ≥ V^0 of 05 §4.2; raise AssertionError otherwise."""
    if not V[m_star] >= V[0]:
        raise AssertionError(f"V[{m_star}] = {V[m_star]} < V[0] = {V[0]}")


# ------------------------------------------------------------ Appendix B evaluator --


def effective_sequential_rounds(batch_sizes: Sequence[int], W: int) -> int:
    """Σ_{k : C_k ≠ ∅} ⌈|C_k| / W⌉ (D-B, 05 §4.3); equals k★ when every |C_k| ≤ W."""
    if W < 1:
        raise ValueError("W must be >= 1")
    if any(b < 0 for b in batch_sizes):
        raise ValueError("batch sizes must be >= 0")
    return sum(-(-b // W) for b in batch_sizes if b > 0)


def episode_penalty(ep: ReplayEpisode) -> float:
    """penalty_i(β) = effective_sequential_rounds / max(1, total_probes), total_probes = N (D-B)."""
    return effective_sequential_rounds(ep.batch_sizes, ep.W) / max(1, ep.n_revealed)


def parallel_penalty(episodes: Sequence[ReplayEpisode]) -> float:
    """parallel_penalty = mean over (world i, β ∈ 𝔅) of penalty_i(β) (D-B, 05 §4.3)."""
    if not episodes:
        raise ValueError("parallel_penalty needs at least one episode")
    return math.fsum(episode_penalty(ep) for ep in episodes) / len(episodes)


def _auc_world(points: Sequence[tuple[float, float]]) -> float:
    """AUC_i = ∫_0^1 max{q : u ≤ x} dx for one world (E5), as an exact step integral."""
    for u, q in points:
        if not 0.0 <= u <= 1.0:
            raise ValueError(f"normalized work u = {u} outside [0, 1]")
        if math.isnan(q):
            raise ValueError("attainment q is NaN")
    xs = sorted({0.0, 1.0, *(u for u, _ in points)})
    area = []
    for a, b in zip(xs, xs[1:]):
        attained = [q for u, q in points if u <= a]
        area.append((max(attained) if attained else 0.0) * (b - a))
    return math.fsum(area)


def pareto_auc_v1(points_by_world: Mapping[str, Sequence[tuple[float, float]]]) -> float:
    """pareto.auc v1 (decision E5, 05 §4.3).

    Per world i: AUC_i = ∫_0^1 max{q_i(β) : β ∈ 𝔅, u_i(β) ≤ u} du (empty set → 0,
    step integral) over its points (u = N/N_max, q = final best s̃); the result
    is the mean over worlds, 0.0 for no worlds.
    """
    if not points_by_world:
        return 0.0
    return math.fsum(_auc_world(p) for p in points_by_world.values()) / len(points_by_world)


def pareto_reward(auc: float, penalty: float, lam: float) -> float:
    """pareto.reward = pareto.auc − λ · parallel_penalty (D-B, 05 §4.3)."""
    return auc - lam * penalty


def anytime_auc(curve: Sequence[tuple[int, float]], N_max: int) -> float:
    """Anytime AUC (05 §6, diagnostic): ∫_0^{N_max} best-so-far(n) dn / N_max.

    `curve` holds (probes, best s̃) points of a step function: 0 before the
    first point, held after the last point up to N_max, points past N_max cut.
    N_max = 0 returns the last best value (0.0 for an empty curve).
    """
    if N_max < 0:
        raise ValueError("N_max must be >= 0")
    pts = sorted(curve, key=lambda p: p[0])
    if any(n < 0 for n, _ in pts):
        raise ValueError("probe counts must be >= 0")
    if N_max == 0:
        return float(pts[-1][1]) if pts else 0.0
    ends = [n for n, _ in pts[1:]] + [N_max]
    area = math.fsum(b * (min(end, N_max) - min(n, N_max))
                     for (n, b), end in zip(pts, ends))
    return area / N_max


# ------------------------------------------------------------------- β rule --


def _clamp01(x: float) -> float:
    """clamp(β, 0, 1) of 05 §4.4, rounded to 9 places to drop float noise."""
    return round(min(1.0, max(0.0, x)), 9)


def next_default_beta(live: Sequence[LiveCycle], sweeps: Sequence[BetaSweep], *,
                      step: float = 0.15, plateau_delta: float = 0.0, window: int = 2,
                      near: float = 0.2, high_beta: float = DEFAULT_BETA,
                      sweep_margin: float = 0.0) -> BetaDecision:
    """Cross-cycle default β of 05 §4.4 (the D-B rule made numeric; a checking criterion).

    Plateau test (σ-like): the last live best minus the best `window` cycles
    earlier is ≤ `plateau_delta` (δ_D). The sweep paired with the latest live
    cycle is compared at its point nearest to β_prev (the reference point):

    - keep β_prev: improving and no sweep point within `near` of β_prev has a
      clearly higher reward (> ref + `sweep_margin`) at no lower attainment;
    - β_prev + step: plateau and a higher β reaches higher attainment
      (> ref + `sweep_margin`) at no lower reward (reasonable work/parallel cost);
    - β_prev − step: plateau, every one of the last `window` cycles already
      baked β ≥ `high_beta`, and a lower β matches the attainment with less work;
    - 0.6: insufficient history (fewer than window + 1 cycles, or a plateau with
      no paired sweep), or conflicting/inconclusive evidence.

    Results are clamped to [0, 1]; step must lie in [0.1, 0.2].
    """
    if not 0.1 <= step <= 0.2:
        raise ValueError("step must lie in [0.1, 0.2]")
    if window < 1:
        raise ValueError("window must be >= 1")
    cycles = sorted(live, key=lambda c: c.iteration)
    if len(cycles) < window + 1:
        return BetaDecision(DEFAULT_BETA, "default",
                            f"insufficient live history: {len(cycles)} cycle(s), "
                            f"need {window + 1}")
    last = cycles[-1]
    beta_prev = last.beta
    gain = last.best_score - cycles[-1 - window].best_score
    improving = gain > plateau_delta
    trend = (f"live best {'improving' if improving else 'plateaued'} "
             f"({gain:+.4f} over {window} cycle(s), band {plateau_delta})")
    paired = [s for s in sweeps if s.iteration == last.iteration and s.points]
    if not paired:
        if improving:
            return BetaDecision(beta_prev, "keep", f"{trend}; no paired sweep to contradict")
        return BetaDecision(DEFAULT_BETA, "default", f"{trend}; no paired beta sweep")
    points = tuple(SweepPoint(*p) for p in paired[-1].points)
    ref = min(points, key=lambda p: abs(p.beta - beta_prev))

    if improving:
        better = [p for p in points if p is not ref and abs(p.beta - beta_prev) <= near
                  and p.reward > ref.reward + sweep_margin and p.attainment >= ref.attainment]
        if not better:
            return BetaDecision(beta_prev, "keep",
                                f"{trend}; sweep shows no clearly better nearby beta")
        return BetaDecision(DEFAULT_BETA, "default",
                            f"{trend}, but sweep favours beta {better[0].beta}: conflict")

    raise_ev = [p for p in points if p.beta > ref.beta
                and p.attainment > ref.attainment + sweep_margin and p.reward >= ref.reward]
    tried_high = all(c.beta >= high_beta for c in cycles[-window:])
    lower_ev = tried_high and any(
        p.beta < ref.beta and p.attainment >= ref.attainment - sweep_margin
        and p.work < ref.work for p in points)
    if raise_ev and lower_ev:
        return BetaDecision(DEFAULT_BETA, "default",
                            f"{trend}; sweep supports both raising and lowering: conflict")
    if raise_ev:
        return BetaDecision(_clamp01(beta_prev + step), "raise",
                            f"{trend}; beta {raise_ev[0].beta} attains more at no lower reward")
    if lower_ev:
        return BetaDecision(_clamp01(beta_prev - step), "lower",
                            f"{trend} at high beta; higher beta adds work without attainment")
    return BetaDecision(DEFAULT_BETA, "default", f"{trend}; sweep evidence inconclusive")


# ------------------------------------------------------------------ grid check --


def validate_grid(plan: GridPlan, ctx: GridPlanningContext, *, replay: bool) -> GridValidity:
    """Grid validation of 05 §4.5 (D-B `plan_grid` runner checks).

    valid ⟺ 1 ≤ B ≤ hard_max_branch_count ∧ 0 ≤ R ≤ hard_max_refine_count.
    In replay, in-support ⟺ B ≤ trace_branch_count ∧ R ≤ trace_refine_count
    (unknown trace support counts as out of support); out-of-support plans earn
    no replay reward. cells = B·(R + 1) for a valid plan, else 0.
    """
    B, R = plan.branch_count, plan.refine_count
    reasons: list[str] = []
    if B < 1:
        reasons.append(f"branch_count {B} < 1")
    if B > ctx.hard_max_branch_count:
        reasons.append(f"branch_count {B} > hard_max_branch_count {ctx.hard_max_branch_count}")
    if R < 0:
        reasons.append(f"refine_count {R} < 0")
    if R > ctx.hard_max_refine_count:
        reasons.append(f"refine_count {R} > hard_max_refine_count {ctx.hard_max_refine_count}")
    valid = not reasons
    in_support = True
    if replay:
        tb, tr = ctx.trace_branch_count, ctx.trace_refine_count
        if tb is None or tr is None:
            in_support = False
            reasons.append("out of support: trace support unknown")
        else:
            if B > tb:
                in_support = False
                reasons.append(f"out of support: branch_count {B} > trace_branch_count {tb}")
            if R > tr:
                in_support = False
                reasons.append(f"out of support: refine_count {R} > trace_refine_count {tr}")
    return GridValidity(valid=valid, in_support=in_support,
                        cells=B * (R + 1) if valid else 0, reasons=tuple(reasons))
