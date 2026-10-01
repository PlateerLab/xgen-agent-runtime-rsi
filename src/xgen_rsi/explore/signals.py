"""Prefix-derived helper signals for exploration policies (04 §2–§3.2).

All helpers read revealed :class:`~xgen_rsi.explore.api.Observation` values
only. They are *signals*: none of them is a closure rule by itself.

Success semantics (Appendix B, restated): an observation is a successful
evaluation iff ``evaluated`` is true, ``error is None`` and ``fail_class ==
"ok"`` — even when ``valid`` is False or ``n_valid``/``n_total`` are missing.
``valid == False`` alone never makes an observation a failure.

Failure vocabulary (E13): ``ok`` plus the failure classes below. Hard classes
point at the environment (a retry from the same parent is unlikely to help);
repairable classes are implementation slips (output mismatch, compile or
runtime errors, resource limits, shape/layout errors) that a later attempt
from the same parent can fix. Unknown classes are treated as repairable by
:func:`failure_kind` — a single unknown failure is not evidence of a hard wall.
"""

from __future__ import annotations

from collections.abc import Sequence

from .api import Observation

SUCCESS_CLASS = "ok"

HARD_FAIL_CLASSES = frozenset({
    "env_failure", "dependency_error", "infra_error", "permission_denied", "unsupported",
})
"""Environment / dependency failures: hard-unrecoverable *candidates* (still need repeat evidence)."""

REPAIRABLE_FAIL_CLASSES = frozenset({
    "wrong_output", "compile_error", "compile_other", "runtime_error", "resource_limit",
    "timeout", "shape_error", "memory_error", "no_submission",
})
"""Implementation failures that are normally repairable from the same parent."""

_REPAIRABLE_HINTS = (
    "mismatch", "shape", "mask", "layout", "shared memory", "out of memory", "resource",
    "nameerror", "typeerror", "indexerror", "keyerror", "attributeerror", "syntax",
    "undefined", "variable", "assert",
)


def is_success(obs: Observation) -> bool:
    """Successful evaluation: evaluated, ``error is None`` and ``fail_class == "ok"``."""
    return bool(obs.evaluated) and obs.error is None and obs.fail_class == SUCCESS_CLASS


def failure_kind(obs: Observation) -> str:
    """``"ok"`` | ``"hard"`` | ``"repairable"`` for one observation.

    ``hard`` iff the class is in :data:`HARD_FAIL_CLASSES` and the error text
    carries no repairable hint; everything else that is not a success is
    ``repairable`` (including ``compile_other`` and zero-valid outputs).
    """
    if is_success(obs):
        return "ok"
    text = (obs.error or "").lower()
    if obs.fail_class in HARD_FAIL_CLASSES and not any(h in text for h in _REPAIRABLE_HINTS):
        return "hard"
    return "repairable"


def probe_improved_vs_parent(obs: Observation, eps: float = 0.0) -> bool:
    """A successful probe whose score beats its parent's (the root's for attempt 0) by > eps."""
    return is_success(obs) and obs.delta_vs_parent is not None and obs.delta_vs_parent > eps


def probe_improved_vs_baseline(obs: Observation, eps: float = 0.0) -> bool:
    """A successful probe whose score beats the baseline score by > eps."""
    return is_success(obs) and obs.delta_vs_baseline is not None and obs.delta_vs_baseline > eps


def branch_failed_hard(obs: Observation) -> bool:
    """Signal: a failed probe with no usable output.

    True iff the probe is not a success and it was not evaluated, produced
    zero valid outputs (``n_valid == 0``), or carries a hard failure class.
    This is a *signal*, not unrecoverability: combine it with
    :func:`failure_kind` (``fail_class`` and ``error``) before closing anything.
    """
    if is_success(obs):
        return False
    return (not obs.evaluated) or obs.n_valid == 0 or obs.fail_class in HARD_FAIL_CLASSES


def branch_promising(obs_or_trajectory: Observation | Sequence[Observation],
                     eps: float = 0.0) -> bool:
    """Signal that a branch deserves more refinement.

    For one observation: a success that improved over the baseline or over
    its parent. For a branch trajectory (observations in attempt order): some
    success improved over the baseline and the latest observation is not a
    hard failure (a repairable latest failure keeps the branch promising).
    """
    if isinstance(obs_or_trajectory, Observation):
        o = obs_or_trajectory
        return probe_improved_vs_baseline(o, eps) or probe_improved_vs_parent(o, eps)
    traj = list(obs_or_trajectory)
    if not traj:
        return False
    if failure_kind(traj[-1]) == "hard":
        return False
    return any(probe_improved_vs_baseline(o, eps) for o in traj)


__all__ = (
    "HARD_FAIL_CLASSES", "REPAIRABLE_FAIL_CLASSES", "SUCCESS_CLASS", "branch_failed_hard",
    "branch_promising", "failure_kind", "is_success", "probe_improved_vs_baseline",
    "probe_improved_vs_parent",
)
