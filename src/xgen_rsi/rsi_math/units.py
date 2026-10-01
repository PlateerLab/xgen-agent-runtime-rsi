"""Unit conversions for instance design (05 §2.4, §2.6; decision D4).

Hyperparameters are easier to reason about per verification unit (one pass,
one criterion) than per unit of Ŝ ∈ [0, 1]. With N_units units in D_evolve·k:

    δ  = n_δ / N_units                    e.g. coding 3 / 178 = 0.01685
    β1 = (relative cost per unit) · N     e.g. coding 0.25 · 178 = 44.5
    w_s = (weight per unit) · N           e.g. workspace 0.1 · 14140 = 1414
"""

from __future__ import annotations


def _check_units(N_units: int) -> None:
    """N_units (verification units in D·k) must be positive."""
    if N_units <= 0:
        raise ValueError("N_units must be > 0")


def delta_from_units(n_units: float, N_units: int) -> float:
    """δ = n_δ / N_units (05 §2.4 unit conversion)."""
    _check_units(N_units)
    return n_units / N_units


def beta1_from_allowance(rel_cost_per_unit: float, N_units: int) -> float:
    """β1 = (allowed relative cost per verification unit) · N_units (05 §2.6, Eq.7)."""
    _check_units(N_units)
    return rel_cost_per_unit * N_units


def ws_from_unit_weight(w_unit: float, N_units: int) -> float:
    """w_s = (in-band weight per verification unit) · N_units (05 §2.6, Eq.17)."""
    _check_units(N_units)
    return w_unit * N_units
