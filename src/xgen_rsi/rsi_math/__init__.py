"""Canonical formula library of the RSI harness (docs/design/33-formula-to-code.md).

Pure functions only: no I/O, no clock, no global state; randomness only through
an argument ``random.Random``. ``paper`` mode reproduces the RRSI reference
implementation (google-research/rrsi @ be50316) exactly; ``xgen`` mode applies
the documented decisions (05 / 02 D1–D18 / 04 E1–E15).
"""

from .bounds import FLOAT_GUARD, can_stop_exactly, early_stop_record_delta, upper_lower
from .calibrate import bootstrap_se, calibrate, pooled
from .dream import (
    DEFAULT_BETA,
    Normalizer,
    anytime_auc,
    assert_non_decreasing,
    effective_sequential_rounds,
    episode_penalty,
    make_normalizer,
    mean_value,
    next_default_beta,
    parallel_penalty,
    pareto_auc_v1,
    pareto_reward,
    replay_value,
    select_policy,
    validate_grid,
)
from .modes import K_ENABLED_XGEN, PAPER, XGEN, Mode, ModeConfig
from .rrsi import (
    GATE_OUTCOMES,
    GENERIC_SIGNALS,
    K_STR,
    MEASURED_OUTCOMES,
    GuardFn,
    K,
    Signals,
    TieMode,
    accepted_counts,
    accepted_edits,
    aggregate,
    attribute,
    check_edit_cardinality,
    classify_diff,
    cost_rule,
    delta_s,
    edit_budget,
    edit_budget_raw,
    edit_records,
    exploration,
    floor_ok,
    has_evidence,
    judge,
    measured,
    normalize_component,
    novelty,
    outcome_of,
    prune_set,
    rate_guard,
    recent_yield,
    relative_cost_change,
    reserved_variants,
    select_round,
    stall_flag,
    text_only,
    tried,
    update_s_star,
    valid_measurement,
)
from .types import (
    AcceptedEdit,
    AttributionRow,
    BetaDecision,
    BetaSweep,
    Calibration,
    Candidate,
    Decision,
    EditRecord,
    EvalResult,
    Exploration,
    GridPlan,
    GridPlanningContext,
    GridValidity,
    LiveCycle,
    PruneTarget,
    ReplayEpisode,
    RRSIParams,
    SweepPoint,
    TaskResult,
    Token,
)
from .units import beta1_from_allowance, delta_from_units, ws_from_unit_weight

__all__ = [
    # types
    "AcceptedEdit", "AttributionRow", "BetaDecision", "BetaSweep", "Calibration",
    "Candidate", "Decision", "EditRecord", "EvalResult", "Exploration", "GridPlan",
    "GridPlanningContext", "GridValidity", "LiveCycle", "PruneTarget", "ReplayEpisode",
    "RRSIParams", "SweepPoint", "TaskResult", "Token",
    # rrsi
    "GATE_OUTCOMES", "GENERIC_SIGNALS", "K", "K_STR", "MEASURED_OUTCOMES", "GuardFn",
    "Signals", "TieMode", "accepted_counts", "accepted_edits", "aggregate", "attribute",
    "check_edit_cardinality", "classify_diff", "cost_rule", "delta_s", "edit_budget",
    "edit_budget_raw", "edit_records", "exploration", "floor_ok", "has_evidence", "judge",
    "measured", "normalize_component", "novelty", "outcome_of", "prune_set", "rate_guard",
    "recent_yield", "relative_cost_change", "reserved_variants", "select_round",
    "stall_flag", "text_only", "tried", "update_s_star", "valid_measurement",
    # calibrate
    "bootstrap_se", "calibrate", "pooled",
    # bounds
    "FLOAT_GUARD", "can_stop_exactly", "early_stop_record_delta", "upper_lower",
    # dream
    "DEFAULT_BETA", "Normalizer", "anytime_auc", "assert_non_decreasing",
    "effective_sequential_rounds", "episode_penalty", "make_normalizer", "mean_value",
    "next_default_beta", "parallel_penalty", "pareto_auc_v1", "pareto_reward",
    "replay_value", "select_policy", "validate_grid",
    # units
    "beta1_from_allowance", "delta_from_units", "ws_from_unit_weight",
    # modes
    "K_ENABLED_XGEN", "Mode", "ModeConfig", "PAPER", "XGEN",
]
