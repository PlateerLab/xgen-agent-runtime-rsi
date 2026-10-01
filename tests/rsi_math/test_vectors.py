"""Check vectors of docs/research/05-formula-reference.md §8 (one test per 33 §6.1 row)."""

from __future__ import annotations

import math

import pytest

from xgen_rsi.rsi_math import (
    Candidate,
    EvalResult,
    ReplayEpisode,
    RRSIParams,
    TaskResult,
    accepted_counts,
    aggregate,
    anytime_auc,
    assert_non_decreasing,
    beta1_from_allowance,
    cost_rule,
    delta_from_units,
    edit_budget,
    edit_budget_raw,
    edit_records,
    episode_penalty,
    make_normalizer,
    novelty,
    parallel_penalty,
    pareto_auc_v1,
    pareto_reward,
    prune_set,
    recent_yield,
    replay_value,
    select_policy,
    select_round,
    stall_flag,
    tried,
    ws_from_unit_weight,
)

# ---------------------------------------------------------------- 8.1 budget --


def test_edit_budget_tables() -> None:
    assert [edit_budget(t, 20, 1, 4) for t in range(20)] == \
        [4, 4, 4, 4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2]
    assert [edit_budget(t, 20, 1, 3) for t in range(20)] == [3] * 10 + [2] * 10
    assert [edit_budget(t, 40, 1, 4) for t in range(40)] == [4] * 16 + [3] * 9 + [2] * 15
    assert edit_budget(20, 20, 1, 4) == 1                       # b_T = b_min
    assert edit_budget_raw(19, 20, 1, 4) == pytest.approx(1.01847, abs=5e-6)
    assert edit_budget(19, 20, 1, 4) == 2
    # b_min is never reached for t in [0, T-1]
    assert min(edit_budget(t, 20, 1, 4) for t in range(20)) == 2


def test_edit_budget_round_guard_and_clamp() -> None:
    # Floating error: the raw value is 2.0000000000000004 where the exact value is 2;
    # without round(v, 9) the ceiling would be 3.
    raw = edit_budget_raw(2, 3, 1, 5)
    assert raw > 2.0 and math.ceil(raw) == 3
    assert edit_budget(2, 3, 1, 5) == 2
    assert edit_budget(25, 20, 1, 4) == 1                       # t clamped to T
    assert edit_budget(-3, 20, 1, 4) == 4                       # t clamped to 0
    assert edit_budget(5, 0, 1, 4) == 4                         # T <= 0 -> b_max
    assert edit_budget_raw(5, -1, 1, 4) == 4.0


# ------------------------------------------------------------------- 8.2 Ŝ --


def test_aggregate_vectors() -> None:
    ev = aggregate({"a": TaskResult([1.0, 0.0]), "b": TaskResult([1.0, 1.0])}, 2)
    assert ev.S == 0.75
    assert ev.n_expected == 4
    assert ev.missing == 0
    assert ev.C is None
    ev = aggregate({"a": TaskResult([0.5, 1.0], [10, 10]),
                    "b": TaskResult([0.0, 0.0], [90, 90])}, 2)
    assert ev.S == 15 / 200 == 0.075


def test_aggregate_missing_trial_and_cost_domain() -> None:
    # A crashed trial: r = 0 inside the denominator, tokens excluded from Ĉ.
    ev = aggregate({"a": TaskResult([1.0, 0.0], tokens=[1000, None], missing=1),
                    "b": TaskResult([1.0, 1.0], tokens=[3000, 0])}, 2)
    assert ev.S == 0.75
    assert ev.missing == 1
    assert ev.C == 2000.0
    assert aggregate({}, 3).S == 0.0
    assert aggregate({"a": TaskResult([1.0], [0.0])}, 1).S == 0.0   # Σw = 0


# ----------------------------------------------------------- 8.3 cost rule --

P = dict(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)


@pytest.mark.parametrize(("dS", "dC", "nu", "ok"), [
    (0.05, 0.10 + 40 * 0.05 - 0.01, 0, True),    # Eq.7, 2.09 <= 2.10
    (0.05, 2.11, 0, False),                      # Eq.7
    (0.0, -0.10, 0, True),                       # Eq.17, 1.5 > 0
    (0.0, 0.10, 0, False),                       # Eq.17, -1.5
    (0.0, 0.0, 1, True),                         # Eq.17, 0.5 > 0
])
def test_cost_rule_vectors(dS: float, dC: float, nu: int, ok: bool) -> None:
    assert cost_rule(dS, dC, nu, 0.02, **P)[0] is ok


def test_cost_rule_boundaries() -> None:
    # ΔS = δ is inside the band (Eq.17), Eq.17 value 0 is rejected, Eq.7 equality passes.
    ok, why = cost_rule(0.02, 0.0, 0, 0.02, **P)
    assert ok and "within delta" in why
    assert cost_rule(0.0, 0.0, 0, 0.02, **P)[0] is False
    assert cost_rule(0.5, 0.1 + 40.0 * 0.5, 0, 0.02, **P)[0] is True


# ----------------------------------------------------------- 8.4 selection --


def _ev(job: str, S: float, C: float | None = 1000.0, **extra: float) -> EvalResult:
    return EvalResult(job=job, k=2, per_task={}, S=S, C=C, n_expected=4, missing=0,
                      extra=extra)


def test_select_round_vectors() -> None:
    cfg = RRSIParams(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)
    inc = _ev("inc", 0.5)
    A = Candidate("A", [{"id": "C1", "component": "prompt"}], _ev("A", 0.7))
    B = Candidate("B", [{"id": "C1", "component": "skill"}], _ev("B", 0.6))
    C = Candidate("C", [{"id": "C1", "component": "config"}], _ev("C", 0.3))
    D = Candidate("D", [{"id": "C1", "component": "memory"}], gate_failure="critic_reject")
    for tie in ("paper", "xgen"):
        winner, ds = select_round([A, B, C, D], inc, 0.55, 0.05, cfg, {}, tie=tie)
        assert winner is A
        assert [d.admissible for d in ds] == [True, True, False, False]
        assert [d.reason_code for d in ds] == ["admissible", "admissible", "floor",
                                               "critic_reject"]
    # 0.7 candidate whose tokens grew by (1 + 0.1 + 40 * 0.2 + 0.5)x: cost rule fails.
    E = Candidate("E", [{"id": "C1", "component": "prompt"}],
                  _ev("E", 0.7, 1000.0 * (1 + 0.1 + 40 * 0.2 + 0.5)))
    winner, ds = select_round([E], inc, 0.55, 0.05, cfg, {})
    assert winner is None
    assert ds[0].reason_code == "cost_rule"
    # A domain guard violation makes A inadmissible too.
    winner, ds = select_round([A], inc, 0.55, 0.05, cfg, {},
                              guard_fn=lambda i, c: ["valid rate dropped"])
    assert winner is None
    assert ds[0].reason_code == "guard"
    assert ds[0].guards == ("valid rate dropped",)


# ------------------------------------------------------ 8.5 history summaries --


def _history() -> list:
    recs = []
    recs += edit_records(0, "A", [{"id": "h1", "component": "prompt", "hypothesis": "h1"}],
                         "ACCEPTED", 0.03, 0.0, True, 0.53, 1000.0)
    recs += edit_records(1, "A", [{"id": "h2", "component": "prompt", "hypothesis": "h2"}],
                         "REJECTED", -0.01, 0.0, False, 0.52, 1000.0)
    recs += edit_records(1, "B", [{"id": "h3", "component": "skill", "hypothesis": "h3"},
                                  {"id": "h4", "component": "memory", "hypothesis": "h4"}],
                         "REJECTED", -0.02, 0.0, False, 0.51, 1000.0)
    recs += edit_records(2, "A", [{"id": "h5", "component": "config", "hypothesis": "h5"}],
                         "critic_reject", None, None, False, None, None)
    return recs


def test_history_summaries_vectors() -> None:
    recs = _history()
    assert tried(recs) == {"prompt", "skill", "memory"}
    g3 = recent_yield(recs, 3, 4)
    assert g3["prompt"] == 0.03 and g3["skill"] == -0.02 and g3["memory"] == -0.02
    assert recent_yield(recs, 5, 4)["prompt"] == -0.01
    assert recent_yield(recs, 6, 4)["prompt"] == -math.inf
    b5 = prune_set(recs, 5, 4)
    assert [p.component for p in b5] == ["memory", "prompt", "skill"]
    prompt = next(p for p in b5 if p.component == "prompt")
    assert [e.hypothesis for e in prompt.accepted_edits_in_incumbent] == ["h1"]
    assert prompt.recent_best_gain == -0.01
    assert next(p for p in prune_set(recs, 6, 4) if p.component == "prompt").recent_best_gain \
        is None
    counts = accepted_counts(recs)
    assert counts["prompt"] == 1
    assert novelty(["client_tool", "prompt"], counts) == 1
    assert novelty(["skill"], {"skill": 2}) == 0


# --------------------------------------------------------- 8.6 stall flag --


def test_stall_flag_vectors() -> None:
    traj = [0.50, 0.53, 0.53, 0.535, 0.60]
    assert stall_flag(traj, 3, 3, 0.02) == 0     # 0.035
    assert stall_flag(traj, 3, 2, 0.02) == 1     # 0.005
    assert stall_flag(traj, 4, 2, 0.02) == 0     # 0.07
    assert stall_flag(traj, 1, 3, 0.02) == 0     # not enough history


# ------------------------------------------------------ 8.7 Table 6 cases --


def test_paper_table6_cases() -> None:
    coding = dict(beta0=0.10, beta1=44.5, w_s=0.0, w_c=15.0, w_n=0.5)
    eng = dict(beta0=0.15, beta1=24.4, w_s=244.0, w_c=2.0, w_n=0.5)
    ok, why = cost_rule(7 / 178, 0.0, 0, 0.017, **coding)            # coding R0-A
    assert ok and "budget 1.850" in why
    for nu in range(5):                                               # coding R0-B
        assert cost_rule(3 / 178, 0.261, nu, 0.017, **coding)[0] is False
    assert 0.0 * (3 / 178) - 15.0 * 0.261 == pytest.approx(-3.915)
    ok, why = cost_rule(6 / 244, 0.016, 0, 0.020, **eng)               # engineering R2
    assert ok and "budget 0.750" in why
    # unit conversions behind those instances
    assert delta_from_units(3, 178) == pytest.approx(0.01685, abs=1e-5)
    assert beta1_from_allowance(0.25, 178) == 44.5
    assert beta1_from_allowance(0.10, 244) == pytest.approx(24.4)
    assert ws_from_unit_weight(0.1, 14140) == pytest.approx(1414.0)


# -------------------------------------------------------- 8.8 Dream toy world --

S_R = 0.40
B0, B1, B2 = [0.50, 0.62, 0.60], [0.30, 0.70], [0.55]
WORLD = [S_R, *B0, *B1, *B2]


def _episodes() -> tuple[ReplayEpisode, ReplayEpisode]:
    norm = make_normalizer(WORLD, S_R)
    a_scores = (B0[0], B0[1], B0[2])
    b_scores = (B0[0], B1[0], B0[1], B1[1])

    def curve(scores: tuple[float, ...]) -> tuple[tuple[int, float], ...]:
        best, out = -math.inf, []
        for n, s in enumerate(scores, start=1):
            best = max(best, norm(s))
            out.append((n, best))
        return tuple(out)

    A = ReplayEpisode("w", 0.2, (1, 1, 1), a_scores, S_R, 3, 3, 2, 6, "empty_batch",
                      curve(a_scores))
    B = ReplayEpisode("w", 0.8, (2, 2), b_scores, S_R, 4, 2, 2, 6, "empty_batch",
                      curve(b_scores))
    return A, B


def test_dream_toy_world() -> None:
    A, B = _episodes()
    raw = make_normalizer(WORLD, S_R, "paper")
    V_A = replay_value(A, beta_cost=0.01, beta_par=0.02, norm=raw)
    V_B = replay_value(B, beta_cost=0.01, beta_par=0.02, norm=raw)
    assert V_A == pytest.approx(0.61, abs=1e-12)
    assert V_B == pytest.approx(0.70, abs=1e-12)
    assert episode_penalty(A) == 1.0
    assert episode_penalty(B) == 0.5
    assert parallel_penalty([A, B]) == 0.75
    norm = make_normalizer(WORLD, S_R)
    u_A, u_B = A.n_revealed / A.N_max, B.n_revealed / B.N_max
    q_A = norm(max(A.revealed_scores))
    q_B = norm(max(B.revealed_scores))
    assert (u_A, round(u_B, 4)) == (0.5, 0.6667)
    assert (round(q_A, 4), q_B) == (0.7333, 1.0)
    auc = pareto_auc_v1({"w": [(u_A, q_A), (u_B, q_B)]})
    assert auc == pytest.approx(0.4556, abs=1e-4)
    assert auc == pytest.approx(0.0 * 0.5 + q_A * (u_B - 0.5) + 1.0 * (1 - u_B), abs=1e-12)
    assert pareto_reward(auc, 0.75, 0.1) == pytest.approx(auc - 0.075)
    for tie in ("paper", "xgen"):
        m_star = select_policy([V_A, V_B], tie=tie)
        assert m_star == 1
        assert_non_decreasing([V_A, V_B], m_star)
    # anytime AUC of A: best-so-far 1/3, then 0.7333 held to N_max = 6
    assert anytime_auc(A.curve, 6) == pytest.approx((norm(0.5) + 4 * q_A) / 6)
    # B hides 0.70 behind a weak first attempt (0.30) on branch b1
    assert norm(B1[0]) == 0.0 and norm(B1[1]) == 1.0
