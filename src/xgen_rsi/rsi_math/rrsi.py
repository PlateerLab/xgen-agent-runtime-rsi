"""RRSI formulas: Eq.3–17 and the computational parts of Algorithms 1 and 2.

Canonical definitions: docs/research/05-formula-reference.md §2–§3. Function
map and signatures: docs/design/33-formula-to-code.md §2.

Everything here is a pure function. With the default arguments
(``tie="paper"`` where a tie mode exists, ``enabled=K``) every function returns
exactly what the reference implementation (google-research/rrsi, commit
be50316: ``schedule``, ``evaluate``, ``selection``, ``history``,
``components``) returns for the same inputs, including the reason strings.

Portions adapted from google-research/rrsi (commit be50316: ``schedule``,
``evaluate``, ``selection``, ``history``, ``components``), Copyright 2026 The
rrsi Authors / Google LLC, Apache License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Any, Final, Literal, TypeGuard

from .types import (
    AcceptedEdit,
    AttributionRow,
    Candidate,
    Decision,
    EditRecord,
    EvalResult,
    Exploration,
    PruneTarget,
    RRSIParams,
    TaskResult,
)

# ------------------------------------------------------------------ vocabulary --

K: Final[tuple[str, ...]] = (
    "prompt", "control_flow", "config", "output_plumbing", "context_mgmt",
    "client_tool", "skill", "memory", "subagent",
)
"""𝒦 (R-Eq12): the component vocabulary, in reference order."""

K_STR: Final[tuple[str, ...]] = ("client_tool", "skill", "memory", "subagent")
"""𝒦_str ⊂ 𝒦 (R-Eq15): components that add machinery."""

MEASURED_OUTCOMES: Final[tuple[str, ...]] = ("ACCEPTED", "REJECTED", "LOST")
GATE_OUTCOMES: Final[tuple[str, ...]] = (
    "critic_reject", "smoke_fail", "eval_invalid", "no_proposal", "not_evaluated",
)

Signals = Sequence[tuple[str, Sequence[str]]]
"""Regex signals: ((component, (pattern, …)), …), searched in order."""

GENERIC_SIGNALS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("memory", (r"\bMemory\(", r"\.remember\(", r"\.recall\(", r"_STATE_DIR")),
    ("skill", (r"skills/", r"SkillRegistry", r"skill_use", r"skill_catalog",
               r"upload_skills")),
    ("client_tool", (r"ToolRegistry", r"register_tool", r"tool_spec", r"CLIENT_TOOLS")),
    ("subagent", (r"\bsubcall\(", r"sub_agent", r"subagent")),
)
"""Signals shared by every domain (reference `GENERIC_SIGNALS`)."""

GuardFn = Callable[[EvalResult, EvalResult], list[str]]
"""g(H_t, H'): (incumbent eval, candidate eval) → violation list; empty = holds (R-C.3)."""

TieMode = Literal["paper", "xgen"]


# ------------------------------------------------------------------- estimator --


def aggregate(per_task: Mapping[str, TaskResult], k: int, *, job: str = "",
              extra: Mapping[str, Any] | None = None) -> EvalResult:
    """Ŝ and Ĉ of Eq.3 in the weighted form of 05 §2.1/§2.2 (decision D1/D2).

    Ŝ = Σ_x Σ_j w·r / Σ_x Σ_j w (0 when the denominator is 0; missing trials are
    r = 0 inside the denominator). Ĉ = mean of the measured c(τ) > 0, None when
    no trial has a positive token count. n_expected = |D|·k.
    """
    num = den = 0.0
    toks: list[float] = []
    missing = 0
    for tr in per_task.values():
        for r, w in zip(tr.rewards, tr.weights):
            num += r * w
            den += w
        toks += [x for x in tr.tokens if isinstance(x, (int, float)) and x > 0]
        missing += tr.missing
    return EvalResult(job=job, k=k, per_task=per_task,
                      S=(num / den) if den else 0.0,
                      C=(sum(toks) / len(toks)) if toks else None,
                      n_expected=len(per_task) * k, missing=missing,
                      extra=dict(extra or {}))


def valid_measurement(ev: EvalResult, invalid_missing_frac: float) -> bool:
    """Measurement validity gate of 05 §2.10: missing(H') ≤ φ · n_expected(H')."""
    return ev.missing <= invalid_missing_frac * ev.n_expected


def delta_s(S_cand: float, S_inc: float) -> float:
    """ΔS = Ŝ(H') − Ŝ(H_t) (R-Eq6)."""
    return S_cand - S_inc


def relative_cost_change(C_cand: float | None, C_inc: float | None) -> float:
    """ΔC = (Ĉ(H') − Ĉ(H_t)) / Ĉ(H_t) (R-Eq6); 0 when either side is missing or 0 (D3)."""
    if not C_cand or not C_inc:
        return 0.0
    return (C_cand - C_inc) / C_inc


# ---------------------------------------------------------------- edit budget --


def edit_budget_raw(t: int, T: int, b_min: int, b_max: int) -> float:
    """The un-rounded b_min + (b_max − b_min)·½(1 + cos(π t / T)) of R-Eq4, t clamped to [0, T]."""
    if T <= 0:
        return float(b_max)
    t = max(0, min(int(t), int(T)))
    return b_min + (b_max - b_min) * 0.5 * (1.0 + math.cos(math.pi * t / T))


def edit_budget(t: int, T: int, b_min: int, b_max: int) -> int:
    """b_t = ⌈round(b_min + (b_max − b_min)·½(1 + cos(π t / T)), 9)⌉ (R-Eq4, 05 §3.1).

    t is clamped to [0, T]; T ≤ 0 returns b_max. The `round(·, 9)` guard keeps
    1.0000000002 at t = T from becoming 2.
    """
    if T <= 0:
        return int(b_max)
    v = edit_budget_raw(t, T, b_min, b_max)
    return int(math.ceil(round(v, 9)))


def check_edit_cardinality(n_edits: int, b_t: int) -> bool:
    """R-Eq9: ‖z_t‖_0 = number of declared independent edits ≤ b_t."""
    return 0 <= n_edits <= b_t


# ------------------------------------------------------------- selection side --


def floor_ok(S_cand: float, S_star: float, delta: float, *, eps: float = 0.0) -> bool:
    """Noise-adjusted floor of R-Eq5: Ŝ(H') ≥ S★ − δ (equality passes).

    ``eps`` absorbs floating-point error so that an exact rational tie (e.g. scores and δ that are
    multiples of 1/178) still counts as equality; ``paper`` mode uses 0 (the reference), ``xgen`` 1e-12.
    """
    return S_cand >= S_star - delta - eps


def cost_rule(dS: float, dC: float, nu: int, delta: float, *, beta0: float,
              beta1: float, w_s: float, w_c: float, w_n: float, eps: float = 0.0) -> tuple[bool, str]:
    """c(H') of Algorithm 2 line 5.

    ΔS > δ:  ΔC ≤ β0 + β1·ΔS                 (R-Eq7, equality passes)
    else:    w_s·ΔS − w_c·ΔC + w_n·ν_t > 0    (R-Eq17, strict; ΔS = δ is in-band)
    """
    if dS > delta + eps:
        budget = beta0 + beta1 * dS
        ok = dC <= budget + eps
        return ok, (f"gain {dS:+.4f} > delta {delta:.4f}; cost change "
                    f"{dC:+.3f} {'<=' if ok else '>'} budget {budget:.3f} "
                    f"(beta0 {beta0} + beta1 {beta1} * dS)")
    shaped = w_s * dS - w_c * dC + w_n * nu
    ok = shaped > eps
    return ok, (f"gain {dS:+.4f} within delta {delta:.4f}; shaped "
                f"{w_s}*dS - {w_c}*dC + {w_n}*nu = {shaped:+.4f} "
                f"{'>' if ok else '<='} 0 (nu={nu})")


def novelty(components: Iterable[str], accepted_counts: Mapping[str, int], *,
            k_str: Collection[str] = K_STR, enabled: Collection[str] = K) -> int:
    """ν_t(H') = |{ℓ ∈ 𝒦_str ∩ comp(H') : N_t(ℓ) = 0}| (R-Eq16).

    comp(H') is taken as a set; kinds outside `enabled` are ignored (decision D-3).
    """
    return sum(1 for c in set(components)
               if c in k_str and c in enabled and accepted_counts.get(c, 0) == 0)


def accepted_counts(records: Sequence[EditRecord], before_t: int | None = None) -> dict[str, int]:
    """N_t(ℓ) = |{i : a_i = 1 ∧ ℓ_i = ℓ ∧ t_i < t}| for every ℓ ∈ 𝒦 (R-Eq16).

    `before_t=None` counts every accepted record (reference
    `incumbent_component_counts`); `before_t=t` is the re-adjudication count.
    """
    counts = {c: 0 for c in K}
    for r in records:
        if (r.accepted and r.component in counts
                and (before_t is None or r.t < before_t)):
            counts[r.component] += 1
    return counts


def rate_guard(max_valid_drop: float = 0.03, max_nosub_rise: float = 0.02, *,
               valid_key: str = "valid_rate", nosub_key: str = "no_sub_rate") -> GuardFn:
    """Engineering-style domain guard g(H_t, H') of R-C.3.

    Violation when valid_rate(H_t) − valid_rate(H') > max_valid_drop or
    no_sub_rate(H') − no_sub_rate(H_t) > max_nosub_rise. Rates are read from
    `EvalResult.extra`; a rate missing on either side is not checked.
    """

    def guard(inc: EvalResult, cand: EvalResult) -> list[str]:
        out: list[str] = []
        vi, vc = inc.extra.get(valid_key), cand.extra.get(valid_key)
        if _num(vi) and _num(vc) and vi - vc > max_valid_drop:
            out.append(f"{valid_key} dropped {vi:.4f} -> {vc:.4f} (> {max_valid_drop})")
        ni, nc = inc.extra.get(nosub_key), cand.extra.get(nosub_key)
        if _num(ni) and _num(nc) and nc - ni > max_nosub_rise:
            out.append(f"{nosub_key} rose {ni:.4f} -> {nc:.4f} (> {max_nosub_rise})")
        return out

    return guard


def _num(x: object) -> TypeGuard[int | float]:
    """A real-valued rate (bools excluded), as read by the guard g of R-C.3."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def judge(cand: Candidate, inc: EvalResult, S_star: float, delta: float,
          cfg: RRSIParams, counts: Mapping[str, int],
          guard_violations: Sequence[str] | None = None, *,
          enabled: Collection[str] = K, k_str: Collection[str] = K_STR, eps: float = 0.0) -> Decision:
    """admissible(H') ⟺ measured ∧ floor_ok (R-Eq5) ∧ c(H') (R-Eq7/17) ∧ g(H_t, H') (R-Eq8).

    The first failing check is recorded, in the reference order
    measurement → floor → cost rule → guard.
    """
    ev = cand.ev
    if ev is None:
        return Decision(cand.variant, False, cand.gate_failure or "not evaluated",
                        cand.gate_failure or "not_evaluated")
    dS = delta_s(ev.S, inc.S)
    dC = relative_cost_change(ev.C, inc.C)
    nov = novelty(cand.components, counts, k_str=k_str, enabled=enabled)
    guards = tuple(guard_violations or ())

    def decide(admissible: bool, reason: str, code: str) -> Decision:
        return Decision(cand.variant, admissible, reason, code, S=ev.S, C=ev.C,
                        delta_S=dS, delta_C=dC, novelty=nov, guards=guards)

    if not floor_ok(ev.S, S_star, delta, eps=eps):
        return decide(False, f"below noise-adjusted floor: S' {ev.S:.4f} < S* "
                             f"{S_star:.4f} - delta {delta:.4f}", "floor")
    ok, why = cost_rule(dS, dC, nov, delta, beta0=cfg.beta0, beta1=cfg.beta1,
                        w_s=cfg.w_s, w_c=cfg.w_c, w_n=cfg.w_n, eps=eps)
    if not ok:
        return decide(False, f"cost rule failed: {why}", "cost_rule")
    if guards:
        return decide(False, "domain guard violated: " + "; ".join(guards), "guard")
    return decide(True, f"admissible: {why}", "admissible")


def _xgen_key(cd: tuple[Candidate, Decision]) -> tuple[float, bool, float, int, str]:
    """Sort key of the D7 tie rule for argmax Ŝ: (−Ŝ, Ĉ missing, Ĉ, edit count, label)."""
    c, d = cd
    S = d.S if d.S is not None else -math.inf
    return (-S, d.C is None, d.C if d.C is not None else 0.0, len(c.edits), c.variant)


def select_round(cands: Sequence[Candidate], inc: EvalResult, S_star: float,
                 delta: float, cfg: RRSIParams, counts: Mapping[str, int],
                 guard_fn: GuardFn | None = None, *, tie: TieMode = "xgen",
                 enabled: Collection[str] = K,
                 k_str: Collection[str] = K_STR, eps: float = 0.0) -> tuple[Candidate | None, list[Decision]]:
    """H_{t+1} = argmax_{H' ∈ 𝓐_t} Ŝ(H') (R-Eq8, R-Alg2); None when 𝓐_t = ∅.

    Ties on Ŝ: ``tie="paper"`` keeps the first admissible maximum (Python
    ``max``, the reference); ``tie="xgen"`` (decision D7) prefers smaller Ĉ
    (missing Ĉ last), then fewer edits, then the variant label.
    """
    decisions: list[Decision] = []
    for c in cands:
        g = guard_fn(inc, c.ev) if (guard_fn is not None and c.ev is not None) else []
        decisions.append(judge(c, inc, S_star, delta, cfg, counts, g,
                               enabled=enabled, k_str=k_str, eps=eps))
    adm = [(c, d) for c, d in zip(cands, decisions) if d.admissible]
    if not adm:
        return None, decisions
    if tie == "paper":
        winner = max(adm, key=lambda cd: cd[1].S if cd[1].S is not None else -math.inf)[0]
    elif tie == "xgen":
        winner = min(adm, key=_xgen_key)[0]
    else:
        raise ValueError(f"unknown tie mode {tie!r}")
    return winner, decisions


def update_s_star(S_star: float, S_next: float) -> float:
    """S★ ← max(S★, Ŝ(H_{t+1})) (R-Eq5, R-Alg2)."""
    return max(S_star, S_next)


def outcome_of(cand: Candidate, winner: Candidate | None, decision: Decision) -> str:
    """Outcome of R-Eq10: ACCEPTED (a_i = 1) / LOST (admissible, beaten) / REJECTED,
    or the gate code (`not_evaluated` by default) for an unmeasured candidate."""
    if cand.ev is None:
        return cand.gate_failure or "not_evaluated"
    if cand is winner:
        return "ACCEPTED"
    return "LOST" if decision.admissible else "REJECTED"


def edit_records(t: int, variant: str, edits: Sequence[Mapping[str, Any]], outcome: str,
                 delta_S: float | None, delta_C: float | None, accepted: bool,
                 S: float | None, C: float | None, detail: str = "", *,
                 early_stopped: bool = False) -> tuple[EditRecord, ...]:
    """The per-edit records of one candidate (R-Eq10, 05 §3.2).

    A candidate bundling n edits yields n records sharing (ΔS, ΔC, a, outcome);
    stored values are rounded like the reference (ΔS, ΔC, Ŝ to 6 places, Ĉ to 1,
    detail to 600 characters). A candidate without edits yields one record C1.
    """
    rows = list(edits) or [{"id": "C1", "component": None, "hypothesis": None}]
    return tuple(
        EditRecord(t=t, variant=variant, edit_id=e.get("id"), component=e.get("component"),
                   hypothesis=e.get("hypothesis") or e.get("mechanism"),
                   delta_S=None if delta_S is None else round(delta_S, 6),
                   delta_C=None if delta_C is None else round(delta_C, 6),
                   accepted=bool(accepted), outcome=outcome,
                   S=None if S is None else round(S, 6),
                   C=None if C is None else round(C, 1),
                   bundle=len(edits), detail=detail[:600] if detail else "",
                   targets_mode=e.get("targets_mode"),
                   predicted_affected=tuple(e.get("predicted_affected") or ()),
                   early_stopped=early_stopped)
        for e in rows)


# ------------------------------------------------------------ history summaries --


def measured(records: Sequence[EditRecord]) -> list[EditRecord]:
    """measured_t = {i ∈ 𝓛_t : ΔS_i ≠ None ∧ ℓ_i ∈ 𝒦} (R-Eq11)."""
    return [r for r in records if r.delta_S is not None and r.component in K]


def tried(records: Sequence[EditRecord]) -> set[str]:
    """𝒯_t = {ℓ_i : i ∈ measured_t} (R-Eq11)."""
    return {r.component for r in measured(records) if r.component is not None}


def recent_yield(records: Sequence[EditRecord], t: int, n_prune: int) -> dict[str, float]:
    """g_t(ℓ) = max{ΔS_i : i ∈ measured_t, ℓ_i = ℓ, t − t_i ≤ n_prune}, −∞ if empty (R-Eq11).

    Defined for every ℓ ∈ 𝒯_t; the window boundary is inclusive.
    """
    g = {c: -math.inf for c in tried(records)}
    for r in measured(records):
        if t - int(r.t) <= n_prune and r.component is not None and r.delta_S is not None:
            g[r.component] = max(g.get(r.component, -math.inf), float(r.delta_S))
    return g


def accepted_edits(records: Sequence[EditRecord]) -> dict[str, list[AcceptedEdit]]:
    """Accepted machinery (a_i = 1) per ℓ ∈ 𝒦, in record order."""
    out: dict[str, list[AcceptedEdit]] = {c: [] for c in K}
    for r in records:
        if r.accepted and r.component in out:
            out[r.component].append(AcceptedEdit(r.t, r.edit_id, r.hypothesis, r.delta_S))
    return out


def prune_set(records: Sequence[EditRecord], t: int, n_prune: int) -> list[PruneTarget]:
    """𝓑_t = {ℓ ∈ 𝒯_t : g_t(ℓ) ≤ 0} (R-Eq14), sorted by name, −∞ included.

    Each target carries g_t(ℓ) (None for −∞, rounded to 5 places) and the
    accepted edits of ℓ (what the proposer may remove).
    """
    g = recent_yield(records, t, n_prune)
    acc = accepted_edits(records)
    out: list[PruneTarget] = []
    for c in sorted(g):
        if g[c] <= 0:
            out.append(PruneTarget(c, None if g[c] == -math.inf else round(g[c], 5),
                                   tuple(acc.get(c, []))))
    return out


def stall_flag(traj: Sequence[float], t: int, w: int, delta: float) -> int:
    """σ_t = 𝟙[traj[t] − traj[t−w] ≤ δ], 0 while t < w (R-Eq13).

    traj[τ] = Ŝ(H_τ); rejected rounds repeat the incumbent score. A negative
    window is outside the domain (the reference raises IndexError there).
    """
    if w < 0:
        raise ValueError("stall window w must be >= 0")
    if t < w or t >= len(traj) or t - w < 0:
        return 0
    return int(traj[t] - traj[t - w] <= delta)


def exploration(stall: int, tried: Collection[str], m_draft: int, *,
                enabled: Collection[str] = K) -> Exploration:
    """𝓔_t = (σ_t, 𝒰_t = 𝒦_enabled \\ 𝒯_t in 𝒦 order, m_draft) (R-Eq13)."""
    untried = tuple(c for c in K if c not in tried and c in enabled)
    return Exploration(sigma=stall, untried=untried, m_draft=m_draft)


def reserved_variants(m: int, m_draft: int, stall: int, untried: Sequence[str]) -> set[int]:
    """Reserved exploration slots: variants v ≥ m − m_draft when σ_t = 1 ∧ 𝒰_t ≠ ∅ (R-Eq13, D11)."""
    if not (stall and untried):
        return set()
    return {v for v in range(m) if v >= m - m_draft}


# ------------------------------------------------------------ tag normalization --

_STRING_LINE = re.compile(r'^[+-]\s*(?:[frb]?["\']|""")')


def text_only(diff: str) -> bool:
    """True when every changed line is a string literal or a comment (05 §2.11, → prompt)."""
    changed = [ln for ln in diff.splitlines()
               if (ln.startswith("+") or ln.startswith("-"))
               and not ln.startswith("+++") and not ln.startswith("---") and ln[1:].strip()]
    if not changed:
        return False
    return all(_STRING_LINE.match(ln) or ln[1:].lstrip().startswith("#") for ln in changed)


def _signals(signals: Signals | None) -> list[tuple[str, Sequence[str]]]:
    """Signal search order of 05 §2.11: domain signals, then the generic ones."""
    return list(signals or []) + list(GENERIC_SIGNALS)


def classify_diff(diff: str, signals: Signals | None = None) -> str:
    """classify(d) of 05 §2.11: prompt if text-only, else first matching signal
    (domain signals, then generic), default prompt."""
    if text_only(diff):
        return "prompt"
    for component, pats in _signals(signals):
        if any(re.search(p, diff) for p in pats):
            return component
    return "prompt"


def has_evidence(component: str, diff: str, signals: Signals | None = None) -> bool:
    """evidence(ℓ, d) by regex: does the diff carry any signal of `component`?"""
    for comp, pats in _signals(signals):
        if comp == component and any(re.search(p, diff) for p in pats):
            return True
    return False


def normalize_component(declared: str | None, touched_kinds: Collection[str], diff: str,
                        signals: Signals | None = None) -> str:
    """normalize(ℓ_decl, d) of 05 §2.11 with manifest evidence first (decision D10).

    1. ℓ_decl ∈ 𝒦 and a touched file declares kind ℓ_decl → ℓ_decl (exact).
    2. ℓ_decl ∈ 𝒦 and the diff carries a regex signal of ℓ_decl → ℓ_decl.
    3. classify: text-only diff → prompt; exactly one touched kind → it;
       first regex signal (domain, then generic); default prompt.

    With empty `touched_kinds` this is exactly the reference `normalize`.
    """
    d = (declared or "").strip().lower()
    kinds = {k for k in touched_kinds if k in K}
    if d in K and d in kinds:
        return d
    if d in K and has_evidence(d, diff, signals):
        return d
    if text_only(diff):
        return "prompt"
    if len(kinds) == 1:
        return next(iter(kinds))
    return classify_diff(diff, signals)


# ------------------------------------------------------------------ attribution --


def attribute(edit: Mapping[str, Any], inc: EvalResult, cand: EvalResult, k: int, *,
              t: int | None = None, variant: str | None = None,
              threshold: float | None = None) -> AttributionRow:
    """Attribution scoreboard row (05 §6, reference `Run.attribute`).

    hit_rate = |predicted ∩ improved| / |predicted| (rounded to 2 places);
    unpredicted_regressions = tasks outside predicted_affected whose mean fell by
    at least the regression threshold 1/k (at most 12).
    """
    thr = 1.0 / max(1, k) if threshold is None else threshold
    pred = [str(p) for p in (edit.get("predicted_affected") or [])
            if str(p) in inc.per_task and str(p) in cand.per_task]
    hits = [p for p in pred if cand.per_task[p].mean > inc.per_task[p].mean]
    drops = [x for x in inc.per_task if x in cand.per_task
             and cand.per_task[x].mean - inc.per_task[x].mean <= -thr]
    return AttributionRow(
        t=t, variant=variant, edit_id=edit.get("id"), component=edit.get("component"),
        hypothesis=str(edit.get("hypothesis"))[:160], n_predicted=len(pred),
        predicted_hit=tuple(hits),
        hit_rate=round(len(hits) / len(pred), 2) if pred else None,
        unpredicted_regressions=tuple([x for x in drops if x not in pred][:12]))
