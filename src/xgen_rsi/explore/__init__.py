"""Exploration layer π_E: the decision interface shared by live runs and replay.

"Same decision interface, different transition" (Dream-RSI §3.2): a policy
written against :mod:`xgen_rsi.explore.api` runs unchanged on a
``LiveQuestion`` (``explore.grid``, real attempts) and on a
``ReplayQuestion`` (``dream.replay``, recorded children revealed).
"""

from .api import (
    ROOT,
    CellMeta,
    GridPlan,
    GridPlanningContext,
    LiveCycleManifest,
    LLMDesignedMethod,
    Observation,
    Question,
    SimResult,
    bootstrap_plan,
    cell_id,
    check_batch,
    finalize_result,
)
from .grid import AttemptResult, LiveGrid, LiveQuestion, solve_live, summarize_live

__all__ = [
    "ROOT", "AttemptResult", "CellMeta", "GridPlan", "GridPlanningContext", "LLMDesignedMethod",
    "LiveCycleManifest", "LiveGrid", "LiveQuestion", "Observation", "Question", "SimResult",
    "bootstrap_plan", "cell_id", "check_batch", "finalize_result", "solve_live", "summarize_live",
]
