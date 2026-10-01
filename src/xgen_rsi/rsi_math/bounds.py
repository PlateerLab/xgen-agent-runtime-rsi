"""Exact-bound early stopping of a candidate evaluation (33 §4, our theorem).

Partial evaluation state: A = Σ w·r over finished trials, B = Σ w over finished
trials, R = Σ w over the trials still to run (all rewards r ∈ [0, 1]).

    Ŝ_max = (A + R) / (B + R)      every remaining trial scores 1
    Ŝ_min =  A      / (B + R)      every remaining trial scores 0 (missing = 0 too)

Theorem. If Ŝ_max < min(S★ − δ, Ŝ_t) the evaluation can stop and the candidate
be recorded REJECTED(floor, early_stopped) with ΔS = Ŝ_max − Ŝ_t: the round's
selection, S★, 𝒯_t, 𝓑_t, N_t, σ_t and 𝒰_t are the same as after the full
evaluation. (Final Ŝ' ≤ Ŝ_max < S★ − δ fixes the floor failure; the recorded ΔS
is negative like the true one, so every sign-based summary is unchanged.)
"""

from __future__ import annotations

FLOAT_GUARD = 1e-9
"""Safety margin on the strict inequality: the full evaluation sums trials in a
different order than (A + R) / (B + R), so the two can differ by a few ulps.
A positive margin only makes stopping rarer; it never breaks the theorem."""


def upper_lower(A: float, B: float, R: float) -> tuple[float, float]:
    """(Ŝ_max, Ŝ_min) = ((A + R)/(B + R), A/(B + R)) of 33 §4; (0, 0) when B + R = 0."""
    if B < 0 or R < 0 or A < 0:
        raise ValueError("A, B and R must be >= 0")
    if A > B * (1 + 1e-12) + 1e-12:
        raise ValueError("A (weighted reward sum) cannot exceed B (weight sum)")
    den = B + R
    if den == 0:
        return 0.0, 0.0
    return (A + R) / den, A / den


def can_stop_exactly(A: float, B: float, R: float, S_star: float, delta: float,
                     S_inc: float, *, margin: float = FLOAT_GUARD) -> bool:
    """33 §4 stop condition: Ŝ_max = (A + R)/(B + R) < min(S★ − δ, Ŝ_t).

    Checked as Ŝ_max + margin < min(S★ − δ, Ŝ_t); ``margin=0`` is the bare
    inequality of the theorem.
    """
    if margin < 0:
        raise ValueError("margin must be >= 0")
    upper, _ = upper_lower(A, B, R)
    return upper + margin < min(S_star - delta, S_inc)


def early_stop_record_delta(A: float, B: float, R: float, S_inc: float) -> float:
    """ΔS recorded for an early-stopped candidate: the upper bound Ŝ_max − Ŝ_t (33 §4)."""
    upper, _ = upper_lower(A, B, R)
    return upper - S_inc
