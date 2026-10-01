"""Mode switch "paper" (reproduce the papers / reference code) vs "xgen" (33 §7).

The two modes differ in exactly the rows of the 33 §7 table and nowhere else:

| row                 | paper                 | xgen                                  |
|---------------------|-----------------------|---------------------------------------|
| argmax tie (D7)     | first candidate       | Ŝ → Ĉ↓ → fewer edits → label          |
| active 𝒦 (D-3)      | all 9 kinds           | `enabled` (default: subagent off)     |
| Dream score (E8)    | raw s_v               | normalized s̃                          |
| Dream root (E1)     | earliest, once        | choose, multiple                      |
| V tie (E11)         | first m               | π^0 first                             |
| early stop (33 §4)  | none                  | exact bound                           |
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from .rrsi import K

Mode = Literal["paper", "xgen"]
RootPolicy = Literal["earliest", "choose"]

K_ENABLED_XGEN: tuple[str, ...] = tuple(c for c in K if c != "subagent")
"""𝒦_enabled under decision D-3 option (a): the subagent kind stays in 𝒦 but is inactive."""


@dataclass(frozen=True)
class ModeConfig:
    """The knobs that differ between modes (33 §7).

    - `select_tie`: tie mode of `select_round` (D7).
    - `enabled`: active component kinds for ν and 𝒰_t (D-3).
    - `normalize_scores`: Dream score s̃ (E8) instead of raw s_v.
    - `root_policy`, `root_multi`: Dream root expansion (E1).
    - `policy_tie`: tie mode of `select_policy` (E11).
    - `early_stop`: exact-bound early stopping (33 §4).
    - `float_eps`: tolerance of the floor / cost-rule comparisons (paper 0 = the reference; xgen 1e-12
      so that exact rational ties are decided as written: ΔS = δ is in-band, S = S★ − δ passes).
    """

    mode: Mode
    select_tie: Mode
    enabled: tuple[str, ...]
    normalize_scores: bool
    root_policy: RootPolicy
    root_multi: bool
    policy_tie: Mode
    early_stop: bool
    float_eps: float = 0.0

    @classmethod
    def for_mode(cls, mode: Mode, *, enabled: Collection[str] | None = None) -> ModeConfig:
        """The canonical configuration of `mode`; `enabled` overrides 𝒦_enabled (xgen only)."""
        if mode == "paper":
            if enabled is not None:
                raise ValueError("paper mode always enables every kind of K")
            return cls(mode="paper", select_tie="paper", enabled=K, normalize_scores=False,
                       root_policy="earliest", root_multi=False, policy_tie="paper",
                       early_stop=False)
        if mode == "xgen":
            kinds = K_ENABLED_XGEN if enabled is None else tuple(c for c in K if c in enabled)
            return cls(mode="xgen", select_tie="xgen", enabled=kinds, normalize_scores=True,
                       root_policy="choose", root_multi=True, policy_tie="xgen",
                       early_stop=True, float_eps=1e-12)
        raise ValueError(f"unknown mode {mode!r}")


PAPER = ModeConfig.for_mode("paper")
XGEN = ModeConfig.for_mode("xgen")
