"""Edge cases, domain checks, xgen-mode paths and the mode switch (33 §7)."""

from __future__ import annotations

import dataclasses
import math
import random

import pytest

from xgen_rsi.rsi_math import (
    FLOAT_GUARD,
    K_ENABLED_XGEN,
    PAPER,
    XGEN,
    BetaSweep,
    Calibration,
    Candidate,
    Decision,
    EditRecord,
    EvalResult,
    GridPlan,
    GridPlanningContext,
    K,
    LiveCycle,
    ModeConfig,
    ReplayEpisode,
    RRSIParams,
    SweepPoint,
    TaskResult,
    anytime_auc,
    assert_non_decreasing,
    bootstrap_se,
    calibrate,
    can_stop_exactly,
    check_edit_cardinality,
    delta_from_units,
    early_stop_record_delta,
    effective_sequential_rounds,
    exploration,
    make_normalizer,
    mean_value,
    next_default_beta,
    normalize_component,
    novelty,
    outcome_of,
    parallel_penalty,
    pareto_auc_v1,
    rate_guard,
    replay_value,
    reserved_variants,
    select_policy,
    select_round,
    stall_flag,
    upper_lower,
    validate_grid,
)

CFG = RRSIParams(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)


def _ev(job: str, S: float, C: float | None = 1000.0, **extra: float) -> EvalResult:
    return EvalResult(job, 2, {}, S, C, 4, 0, extra)


# ------------------------------------------------------------------------ types --


def test_task_result_validation_and_json() -> None:
    with pytest.raises(ValueError):
        TaskResult([1.0, 0.0], [1.0])
    with pytest.raises(ValueError):
        TaskResult([1.0], tokens=[1, 2])
    with pytest.raises(ValueError):
        TaskResult([1.0], missing=-1)
    tr = TaskResult([0.5, 1.0], [2.0, 2.0], [10, None], missing=1, extra={"a": 1})
    assert TaskResult.from_json(tr.to_json()) == tr
    assert TaskResult([1.0]).weights == (1.0,) and TaskResult([1.0]).tokens == (None,)
    assert TaskResult([], [0.0] * 0).mean == 0.0


def test_value_types_json() -> None:
    d = Decision("A", True, "ok", "admissible", 0.5, None, 0.1, 0.0, 1, ["g"])
    assert d.guards == ("g",) and d.to_json()["guards"] == ["g"]
    rec = EditRecord.from_json({"t": 2, "accepted": 1, "component": "skill"})
    assert (rec.variant, rec.outcome, rec.bundle, rec.early_stopped) == ("", "", 0, False)
    assert rec.accepted is True
    sweep = BetaSweep(1, [(0.2, 0.5, 3.0, 0.4)])
    assert sweep.points == (SweepPoint(0.2, 0.5, 3.0, 0.4),)
    cal = Calibration(0.1, 2.0, 0.05, 0.05, 0.03, "bootstrap", 1, 2, 3, 0.5, None)
    assert "S_per_eval" not in cal.to_json()
    c = Candidate("A", [{"id": "C1", "component": "skill"}, {"id": "C2", "component": ""}])
    assert c.components == ["skill"]


@pytest.mark.parametrize("kw", [
    dict(k=0), dict(b_min=3, b_max=2), dict(m=2, m_draft=3), dict(w=0), dict(n_prune=-1),
    dict(delta=-0.1), dict(beta1=-1.0), dict(invalid_missing_frac=1.5),
])
def test_rrsi_params_validation(kw: dict) -> None:
    with pytest.raises(ValueError):
        RRSIParams(**kw)


@pytest.mark.parametrize("kw", [
    dict(n_revealed=2), dict(rounds=-1), dict(W=0), dict(batch_sizes=(1, -1)),
])
def test_replay_episode_validation(kw: dict) -> None:
    base = dict(world_id="w", beta=0.5, batch_sizes=(1,), revealed_scores=(0.5,),
                root_score=0.4, n_revealed=1, rounds=1, W=2, N_max=3)
    base.update(kw)
    with pytest.raises(ValueError):
        ReplayEpisode(**base)


# ------------------------------------------------------------------------- rrsi --


def test_edit_cardinality() -> None:
    assert check_edit_cardinality(2, 2)
    assert not check_edit_cardinality(3, 2)
    assert not check_edit_cardinality(-1, 2)


def test_rate_guard() -> None:
    g = rate_guard()
    inc = _ev("i", 0.5, valid_rate=0.95, no_sub_rate=0.01)
    assert g(inc, _ev("c", 0.6, valid_rate=0.93, no_sub_rate=0.02)) == []
    bad = g(inc, _ev("c", 0.6, valid_rate=0.90, no_sub_rate=0.05))
    assert len(bad) == 2 and "valid_rate" in bad[0] and "no_sub_rate" in bad[1]
    assert g(inc, _ev("c", 0.6)) == []                          # rate missing: unchecked
    assert g(_ev("i", 0.5, valid_rate=True), _ev("c", 0.6, valid_rate=0.0)) == []


def test_select_round_xgen_ties_and_bad_mode() -> None:
    inc = _ev("inc", 0.5)
    e1 = [{"id": "C1", "component": "prompt"}]
    e2 = [*e1, {"id": "C2", "component": "config"}]
    A = Candidate("A", e1, _ev("A", 0.7, 1200.0))
    B = Candidate("B", e1, _ev("B", 0.7, 1000.0))
    C = Candidate("C", e1, _ev("C", 0.7, None))
    D = Candidate("D", e2, _ev("D", 0.7, 1000.0))
    E = Candidate("E", e1, _ev("E", 0.7, 1000.0))
    assert select_round([A, B, C], inc, 0.5, 0.05, CFG, {}, tie="paper")[0] is A
    assert select_round([A, B, C], inc, 0.5, 0.05, CFG, {}, tie="xgen")[0] is B   # Ĉ smaller
    assert select_round([C, A], inc, 0.5, 0.05, CFG, {}, tie="xgen")[0] is A      # None last
    assert select_round([D, B], inc, 0.5, 0.05, CFG, {}, tie="xgen")[0] is B      # fewer edits
    assert select_round([E, B], inc, 0.5, 0.05, CFG, {}, tie="xgen")[0] is B      # label
    with pytest.raises(ValueError):
        select_round([A], inc, 0.5, 0.05, CFG, {}, tie="bogus")  # type: ignore[arg-type]
    winner, ds = select_round([A, Candidate("Z", e1)], inc, 0.5, 0.05, CFG, {})
    assert outcome_of(A, winner, ds[0]) == "ACCEPTED"
    assert outcome_of(Candidate("Z", e1), winner, ds[1]) == "not_evaluated"
    assert ds[1].reason == "not evaluated" and ds[1].reason_code == "not_evaluated"
    Y = Candidate("Y", e1, _ev("Y", 0.65))
    R = Candidate("R", e1, _ev("R", 0.1))
    winner, ds = select_round([A, Y, R], inc, 0.5, 0.05, CFG, {})
    assert [outcome_of(c, winner, d) for c, d in zip([A, Y, R], ds)] == \
        ["ACCEPTED", "LOST", "REJECTED"]


def test_xgen_enabled_kinds() -> None:
    assert novelty(["subagent", "skill"], {}) == 2
    assert novelty(["subagent", "skill"], {}, enabled=K_ENABLED_XGEN) == 1
    assert "subagent" in exploration(1, set(), 1).untried
    assert "subagent" not in exploration(1, set(), 1, enabled=K_ENABLED_XGEN).untried
    inc = _ev("inc", 0.5)
    cand = Candidate("A", [{"id": "C1", "component": "subagent"}], _ev("A", 0.5))
    ds_paper = select_round([cand], inc, 0.5, 0.05, CFG, {})[1]
    ds_xgen = select_round([cand], inc, 0.5, 0.05, CFG, {}, enabled=XGEN.enabled)[1]
    assert (ds_paper[0].novelty, ds_paper[0].admissible) == (1, True)
    assert (ds_xgen[0].novelty, ds_xgen[0].admissible) == (0, False)


def test_stall_and_reserved() -> None:
    with pytest.raises(ValueError):
        stall_flag([0.5, 0.5], 1, -1, 0.0)
    assert reserved_variants(2, 1, 1, ["skill"]) == {1}
    assert reserved_variants(3, 2, 1, ["skill"]) == {1, 2}
    assert reserved_variants(2, 1, 0, ["skill"]) == set()
    assert reserved_variants(2, 1, 1, []) == set()


def test_normalize_component_manifest_paths() -> None:
    code = "+x = compute(y)"
    # declared kind confirmed by the manifest even without a regex signal
    assert normalize_component("skill", {"skill"}, code) == "skill"
    # declared kind not touched and not evidenced: reclassified from the single touched kind
    assert normalize_component("skill", {"config"}, code) == "config"
    # text-only edits are prompt edits whatever kind was touched
    assert normalize_component("skill", {"config"}, '+"Be concise."') == "prompt"
    # several touched kinds: fall back to regex signals, then prompt
    assert normalize_component(None, {"config", "memory"}, "+m = Memory(p)") == "memory"
    assert normalize_component(None, {"config", "memory", "bogus"}, code) == "prompt"
    assert normalize_component("Memory ", set(), "+m = Memory(p)") == "memory"


# ----------------------------------------------------------------------- bounds --


def test_bounds_edges() -> None:
    assert upper_lower(0.0, 0.0, 0.0) == (0.0, 0.0)
    assert upper_lower(1.0, 2.0, 2.0) == (0.75, 0.25)
    for bad in ((-1.0, 1.0, 1.0), (1.0, -1.0, 1.0), (1.0, 1.0, -1.0), (2.0, 1.0, 0.0)):
        with pytest.raises(ValueError):
            upper_lower(*bad)
    with pytest.raises(ValueError):
        can_stop_exactly(0.0, 1.0, 1.0, 0.5, 0.0, 0.5, margin=-1.0)
    # Ŝ_max = 0.25 < min(0.5 - 0.05, 0.6)
    assert can_stop_exactly(0.0, 3.0, 1.0, 0.5, 0.05, 0.6)
    # bare inequality vs the float guard: Ŝ_max exactly one guard below the threshold
    assert can_stop_exactly(0.0, 3.0, 1.0, 0.25 + FLOAT_GUARD / 2, 0.0, 1.0, margin=0.0)
    assert not can_stop_exactly(0.0, 3.0, 1.0, 0.25 + FLOAT_GUARD / 2, 0.0, 1.0)
    # S_inc below the floor: stopping needs Ŝ_max < Ŝ_t as well
    assert not can_stop_exactly(0.0, 3.0, 1.0, 0.9, 0.0, 0.2)
    assert early_stop_record_delta(0.0, 3.0, 1.0, 0.6) == pytest.approx(-0.35)


# -------------------------------------------------------------------- calibrate --


def test_calibrate_edges() -> None:
    with pytest.raises(ValueError):
        calibrate([])
    ev = EvalResult("b", 2, {"a": TaskResult([1.0, 0.0]), "b": TaskResult([])}, 0.5, None, 4, 0)
    assert bootstrap_se(ev, reps=50) == bootstrap_se(ev, reps=50, rng=random.Random(7))
    assert bootstrap_se(ev, reps=50, seed=8) == bootstrap_se(ev, reps=50, rng=random.Random(8))
    # identical repeated evaluations: sd_null = 0 -> bootstrap fallback
    same = calibrate([ev, ev], reps=50)
    assert same.method == "repeated base evaluations" and same.max_abs_diff == 0.0
    assert same.sd_null == same.sd_null_bootstrap


# -------------------------------------------------------------------------- units --


def test_units_domain() -> None:
    with pytest.raises(ValueError):
        delta_from_units(3, 0)


# ------------------------------------------------------------------------ dream --


def test_normalizer_edges() -> None:
    with pytest.raises(ValueError):
        make_normalizer([0.5], 0.4, "bogus")  # type: ignore[arg-type]
    below = make_normalizer([0.1, 0.2], 0.4)          # s_ceil <= s_base: indicator
    assert (below(0.39), below(0.4), below(0.9)) == (0.0, 1.0, 1.0)
    empty = make_normalizer([], 0.4)
    assert (empty(0.3), empty(0.5)) == (0.0, 1.0)


def test_replay_value_edges() -> None:
    norm = make_normalizer([0.4, 0.7], 0.4)
    empty = ReplayEpisode("w", 0.5, (), (), None, 0, 0, 2, 4)
    assert replay_value(empty, beta_cost=0.1, beta_par=0.1, norm=norm) == 0.0
    root_only = ReplayEpisode("w", 0.5, (), (), 0.7, 0, 0, 2, 4)
    assert replay_value(root_only, beta_cost=0.1, beta_par=0.1, norm=norm) == 1.0
    assert parallel_penalty([empty]) == 0.0
    with pytest.raises(ValueError):
        parallel_penalty([])


def test_mean_and_selection_edges() -> None:
    assert mean_value([0.5, 0.7]) == pytest.approx(0.6)
    with pytest.raises(ValueError):
        mean_value([])
    for bad in ([], [math.nan]):
        with pytest.raises(ValueError):
            select_policy(bad)
    with pytest.raises(ValueError):
        select_policy([0.5], tie="bogus")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        select_policy([0.5], tol=-1.0)
    assert select_policy([0.5, 0.505], tol=0.01) == 0              # near-tie: keep π^0
    assert select_policy([0.5, 0.505], tie="paper", tol=0.01) == 1
    assert select_policy([0.5, 0.6, 0.6]) == 1
    with pytest.raises(AssertionError):
        assert_non_decreasing([0.5, 0.4], 1)


def test_effective_sequential_rounds_edges() -> None:
    assert effective_sequential_rounds([5, 2, 0], 2) == 4
    with pytest.raises(ValueError):
        effective_sequential_rounds([1], 0)
    with pytest.raises(ValueError):
        effective_sequential_rounds([-1], 2)


def test_pareto_auc_edges() -> None:
    assert pareto_auc_v1({}) == 0.0
    assert pareto_auc_v1({"w": []}) == 0.0
    assert pareto_auc_v1({"w": [(0.0, 1.0)]}) == 1.0
    assert pareto_auc_v1({"a": [(0.0, 1.0)], "b": [(0.5, 1.0)]}) == 0.75
    with pytest.raises(ValueError):
        pareto_auc_v1({"w": [(1.5, 0.5)]})
    with pytest.raises(ValueError):
        pareto_auc_v1({"w": [(0.5, math.nan)]})


def test_anytime_auc_edges() -> None:
    assert anytime_auc([], 5) == 0.0
    assert anytime_auc([], 0) == 0.0
    assert anytime_auc([(1, 0.2), (2, 0.5)], 0) == 0.5
    # points past N_max are cut; unsorted input is sorted by probes
    assert anytime_auc([(3, 1.0), (1, 0.5)], 2) == pytest.approx(0.25)
    assert anytime_auc([(0, 1.0)], 4) == 1.0
    with pytest.raises(ValueError):
        anytime_auc([(1, 0.5)], -1)
    with pytest.raises(ValueError):
        anytime_auc([(-1, 0.5)], 3)


def _live(*rows: tuple[float, float]) -> list[LiveCycle]:
    return [LiveCycle(i, s, b) for i, (s, b) in enumerate(rows, start=1)]


def test_next_default_beta_branches() -> None:
    with pytest.raises(ValueError):
        next_default_beta([], [], step=0.3)
    with pytest.raises(ValueError):
        next_default_beta([], [], window=0)
    improving = _live((0.50, 0.4), (0.55, 0.4), (0.60, 0.4))
    plateau = _live((0.60, 0.4), (0.60, 0.4), (0.60, 0.4))
    high = _live((0.60, 0.8), (0.60, 0.8), (0.60, 0.8))

    d = next_default_beta(improving[:2], [])
    assert (d.beta, d.branch) == (0.6, "default") and "insufficient" in d.reason
    assert next_default_beta(improving, []).branch == "keep"
    assert next_default_beta(improving, [BetaSweep(3, [])]).branch == "keep"   # empty sweep
    assert next_default_beta(plateau, []).branch == "default"
    assert next_default_beta(plateau, [BetaSweep(2, [(0.4, 0.6, 20, 0.5)])]).branch == "default"

    calm = BetaSweep(3, [(0.2, 0.5, 10, 0.45), (0.4, 0.6, 20, 0.55), (0.6, 0.62, 40, 0.50)])
    d = next_default_beta(improving, [calm])
    assert (d.beta, d.branch) == (0.4, "keep")
    better = BetaSweep(3, [(0.4, 0.6, 20, 0.55), (0.6, 0.7, 25, 0.65)])
    d = next_default_beta(improving, [better])
    assert (d.beta, d.branch) == (0.6, "default") and "conflict" in d.reason

    up = BetaSweep(3, [(0.4, 0.6, 20, 0.55), (0.6, 0.7, 30, 0.6)])
    d = next_default_beta(plateau, [up])
    assert (d.beta, d.branch) == (0.55, "raise")
    d = next_default_beta(_live((0.6, 0.95), (0.6, 0.95), (0.6, 0.95)),
                          [BetaSweep(3, [(0.95, 0.6, 20, 0.5), (1.0, 0.8, 30, 0.6)])], step=0.2)
    assert (d.beta, d.branch) == (1.0, "raise")                              # clamped

    down = BetaSweep(3, [(0.8, 0.6, 50, 0.5), (0.6, 0.6, 30, 0.55)])
    d = next_default_beta(high, [down])
    assert (d.beta, d.branch) == (0.65, "lower")
    both = BetaSweep(3, [(0.6, 0.6, 30, 0.55), (0.8, 0.6, 50, 0.5), (1.0, 0.7, 60, 0.5)])
    d = next_default_beta(high, [both])
    assert (d.beta, d.branch) == (0.6, "default") and "conflict" in d.reason
    flat = BetaSweep(3, [(0.4, 0.6, 20, 0.55), (0.6, 0.6, 30, 0.5)])
    d = next_default_beta(plateau, [flat])
    assert (d.beta, d.branch) == (0.6, "default") and "inconclusive" in d.reason
    # lowering needs the high β to have been baked in every plateau cycle
    mixed = _live((0.60, 0.4), (0.60, 0.4), (0.60, 0.8))
    assert next_default_beta(mixed, [BetaSweep(3, list(down.points))]).branch == "default"


def test_validate_grid() -> None:
    ctx = GridPlanningContext(32, 19, 8, 4, 3, trace_branch_count=10, trace_refine_count=10)
    ok = validate_grid(GridPlan(10, 10), ctx, replay=True)
    assert (ok.valid, ok.in_support, ok.cells, ok.rewardable) == (True, True, 110, True)
    bad = validate_grid(GridPlan(0, -1), ctx, replay=False)
    assert (bad.valid, bad.cells, len(bad.reasons)) == (False, 0, 2)
    over = validate_grid(GridPlan(33, 20), ctx, replay=False)
    assert not over.valid and len(over.reasons) == 2 and over.in_support
    oos = validate_grid(GridPlan(11, 11), ctx, replay=True)
    assert oos.valid and not oos.in_support and not oos.rewardable and len(oos.reasons) == 2
    live = validate_grid(GridPlan(11, 11), dataclasses.replace(ctx, trace_branch_count=None),
                         replay=False)
    assert live.rewardable
    unknown = validate_grid(GridPlan(5, 5), dataclasses.replace(ctx, trace_refine_count=None),
                            replay=True)
    assert not unknown.in_support


# ------------------------------------------------------------------------ modes --


def test_mode_differences_are_exactly_the_33_7_rows() -> None:
    differing = {f.name for f in dataclasses.fields(ModeConfig)
                 if getattr(PAPER, f.name) != getattr(XGEN, f.name)}
    assert differing == {"mode", "select_tie", "enabled", "normalize_scores", "root_policy",
                         "root_multi", "policy_tie", "early_stop", "float_eps"}
    assert PAPER.float_eps == 0.0 and XGEN.float_eps == 1e-12
    assert PAPER.enabled == K and XGEN.enabled == K_ENABLED_XGEN
    assert (PAPER.root_policy, PAPER.root_multi) == ("earliest", False)
    assert (XGEN.root_policy, XGEN.root_multi) == ("choose", True)
    assert ModeConfig.for_mode("xgen", enabled=["skill", "prompt"]).enabled == ("prompt", "skill")
    with pytest.raises(ValueError):
        ModeConfig.for_mode("paper", enabled=K)
    with pytest.raises(ValueError):
        ModeConfig.for_mode("bogus")  # type: ignore[arg-type]


def test_exact_rational_ties_are_decided_as_written_in_xgen_mode() -> None:
    """δ 와 점수가 1/178 의 배수일 때: ΔS = δ 는 띠 안(Eq.17), S = S★ − δ 는 바닥 통과 — 부동소수 오차로 뒤집히지 않는다."""
    from xgen_rsi.rsi_math import cost_rule, floor_ok

    d = 3 / 178
    for n in range(3, 179):
        assert floor_ok((n - 3) / 178, n / 178, d, eps=XGEN.float_eps)
        _, why = cost_rule(n / 178 - (n - 3) / 178, 0.0, 0, d, beta0=0.1, beta1=40, w_s=100, w_c=15, w_n=0.5,
                           eps=XGEN.float_eps)
        assert "within" in why
    # 같은 후보(ΔS = 0, ΔC = 0, ν = 0)는 띠 안 규칙의 엄격 부등식으로 반려된다
    assert cost_rule(0.0, 1e-16, 0, d, beta0=0.1, beta1=40, w_s=100, w_c=15, w_n=0.5, eps=XGEN.float_eps)[0] is False
