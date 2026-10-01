"""Noise band δ (05 §2.4, reference `rrsi/calibrate.py`, decision D6).

    R ≥ 2:  sd_null = stdev(Ŝ^(1..R)) · √2
    R = 1:  sd_null = √2 · se_boot · √(k_pooled / k),  se_boot = pstdev of 2000
            within-task trial bootstraps (weights respected, seed 7)
    δ = z · sd_null,  z = 2.0

The bootstrap draws from the RNG in exactly the reference order (reps → tasks
in insertion order → one `randrange(n)` per trial), so the same inputs give the
same δ bit for bit.

Portions adapted from google-research/rrsi (commit be50316,
``rrsi/calibrate.py``: the calibration procedure and its bootstrap),
Copyright 2026 The rrsi Authors / Google LLC, Apache License 2.0; modified by
PlateerLab.
"""

from __future__ import annotations

import math
import random
import statistics as st
from collections.abc import Sequence

from .rrsi import aggregate
from .types import Calibration, EvalResult, TaskResult, Token

DEFAULT_SEED = 7
DEFAULT_REPS = 2000


def bootstrap_se(ev: EvalResult, reps: int = DEFAULT_REPS,
                 rng: random.Random | None = None, *, seed: int = DEFAULT_SEED) -> float:
    """se(Ŝ) by resampling trials with replacement within each task (05 §2.4, R = 1 path).

    Each replicate recomputes Ŝ* = Σ w·r / Σ w over the resampled trials; the
    result is pstdev(Ŝ*_1..reps) (0 for fewer than two replicates). `rng=None`
    uses a fresh ``random.Random(seed)``; the argument RNG is the only source of
    randomness.
    """
    rng = random.Random(seed) if rng is None else rng
    tasks = [tr for tr in ev.per_task.values() if tr.rewards]
    vals: list[float] = []
    for _ in range(reps):
        num = den = 0.0
        for tr in tasks:
            n = len(tr.rewards)
            for _j in range(n):
                i = rng.randrange(n)
                num += tr.rewards[i] * tr.weights[i]
                den += tr.weights[i]
        vals.append(num / den if den else 0.0)
    return st.pstdev(vals) if len(vals) > 1 else 0.0


def pooled(evals: Sequence[EvalResult]) -> EvalResult:
    """Concatenate the trials of several evaluations of the same harness (k_pooled = Σ k)."""
    rewards: dict[str, list[float]] = {}
    weights: dict[str, list[float]] = {}
    tokens: dict[str, list[Token]] = {}
    missing: dict[str, int] = {}
    for ev in evals:
        for t, tr in ev.per_task.items():
            rewards.setdefault(t, []).extend(tr.rewards)
            weights.setdefault(t, []).extend(tr.weights)
            tokens.setdefault(t, []).extend(tr.tokens)
            missing[t] = missing.get(t, 0) + tr.missing
    per = {t: TaskResult(rewards=rewards[t], weights=weights[t], tokens=tokens[t],
                         missing=missing[t]) for t in rewards}
    return aggregate(per, sum(e.k for e in evals), job="pooled")


def calibrate(evals: Sequence[EvalResult], z: float = 2.0, reps: int = DEFAULT_REPS, *,
              seed: int = DEFAULT_SEED) -> Calibration:
    """δ = z · sd_null from R ≥ 1 evaluations of the base harness H_0 (05 §2.4).

    With R ≥ 2 and sd_null > 0 the direct observation is used, otherwise the
    bootstrap value. Rounding (6 places) follows the reference.
    """
    if not evals:
        raise ValueError("no base evaluations")
    S_per_eval: tuple[float, ...] | None = None
    max_abs_diff: float | None = None
    if len(evals) >= 2:
        scores = [e.S for e in evals]
        diffs = [abs(a - b) for i, a in enumerate(scores) for b in scores[i + 1:]]
        sd_null = st.stdev(scores) * math.sqrt(2)
        method = "repeated base evaluations"
        S_per_eval, max_abs_diff = tuple(scores), max(diffs)
    else:
        sd_null = 0.0
        method = "bootstrap over trials of one base evaluation"
    pooled_ev = pooled(evals)
    se = bootstrap_se(pooled_ev, reps=reps, rng=random.Random(seed))
    sd_boot = math.sqrt(2) * se * math.sqrt(pooled_ev.k / evals[0].k)
    sd_use = sd_null if (len(evals) >= 2 and sd_null > 0) else sd_boot
    return Calibration(delta=round(z * sd_use, 6), z=z, sd_null=round(sd_use, 6),
                       sd_null_bootstrap=round(sd_boot, 6), se_bootstrap=round(se, 6),
                       method=method, n_evals=len(evals), k=evals[0].k,
                       n_tasks=len(pooled_ev.per_task), S_base=round(evals[0].S, 6),
                       C_base=evals[0].C, S_per_eval=S_per_eval, max_abs_diff=max_abs_diff)
