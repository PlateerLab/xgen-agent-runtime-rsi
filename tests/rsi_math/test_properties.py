"""Property-based invariants of 33 §6.3 (hypothesis)."""

from __future__ import annotations

import math
import random

from hypothesis import event, given, settings
from hypothesis import strategies as st

from xgen_rsi.rsi_math import (
    Candidate,
    EditRecord,
    EvalResult,
    K,
    ReplayEpisode,
    RRSIParams,
    TaskResult,
    accepted_counts,
    aggregate,
    assert_non_decreasing,
    can_stop_exactly,
    cost_rule,
    early_stop_record_delta,
    edit_budget,
    edit_records,
    episode_penalty,
    exploration,
    floor_ok,
    make_normalizer,
    parallel_penalty,
    pareto_auc_v1,
    prune_set,
    select_policy,
    select_round,
    tried,
    update_s_star,
    upper_lower,
)

unit = st.floats(0.0, 1.0, allow_nan=False)
small = st.floats(-1.0, 1.0, allow_nan=False)
nonneg = st.floats(0.0, 100.0, allow_nan=False)

# ------------------------------------------------------------------ edit_budget --


@settings(max_examples=500, deadline=None)
@given(T=st.integers(1, 200), b=st.tuples(st.integers(1, 10), st.integers(1, 10)))
def test_edit_budget_monotone_and_bounded(T: int, b: tuple[int, int]) -> None:
    b_min, b_max = min(b), max(b)
    seq = [edit_budget(t, T, b_min, b_max) for t in range(T + 1)]
    assert seq[0] == b_max
    assert seq[T] == b_min
    assert all(b_min <= x <= b_max for x in seq)
    assert all(x >= y for x, y in zip(seq, seq[1:]))


# -------------------------------------------------------------------- aggregate --


@st.composite
def task_results(draw: st.DrawFn, max_tasks: int = 10) -> dict[str, TaskResult]:
    n = draw(st.integers(1, max_tasks))
    k = draw(st.integers(1, 4))
    per = {}
    for i in range(n):
        rewards = draw(st.lists(unit, min_size=k, max_size=k))
        weights = draw(st.lists(st.floats(0.01, 100.0), min_size=k, max_size=k))
        per[f"t{i}"] = TaskResult(rewards, weights)
    return per


@settings(max_examples=500, deadline=None)
@given(per=task_results(), data=st.data())
def test_aggregate_bounds_and_missing_never_helps(per: dict[str, TaskResult],
                                                  data: st.DataObject) -> None:
    k = max(len(tr.rewards) for tr in per.values())
    ev = aggregate(per, k)
    assert 0.0 <= ev.S <= 1.0
    # turn one trial into a missing trial (r = 0, weight kept): Ŝ must not increase
    task = data.draw(st.sampled_from(sorted(per)))
    tr = per[task]
    j = data.draw(st.integers(0, len(tr.rewards) - 1))
    rewards = list(tr.rewards)
    rewards[j] = 0.0
    per2 = dict(per)
    per2[task] = TaskResult(rewards, tr.weights, missing=tr.missing + 1)
    ev2 = aggregate(per2, k)
    assert ev2.S <= ev.S
    assert ev2.missing == ev.missing + 1


# -------------------------------------------------------------------- cost_rule --


@settings(max_examples=1000, deadline=None)
@given(dS=small, dC=st.floats(-1.0, 50.0), shrink=st.floats(0.0, 10.0), nu=st.integers(0, 4),
       extra_nu=st.integers(0, 4), delta=st.floats(0.0, 0.2),
       params=st.tuples(nonneg, nonneg, nonneg, nonneg, nonneg))
def test_cost_rule_monotone(dS: float, dC: float, shrink: float, nu: int, extra_nu: int,
                            delta: float, params: tuple[float, ...]) -> None:
    b0, b1, ws, wc, wn = params
    kw = dict(beta0=b0, beta1=b1, w_s=ws, w_c=wc, w_n=wn)
    ok, _ = cost_rule(dS, dC, nu, delta, **kw)
    # lowering ΔC never turns true into false
    if ok:
        assert cost_rule(dS, dC - shrink, nu, delta, **kw)[0]
    # raising ν never turns an in-band true into false
    if ok and dS <= delta:
        assert cost_rule(dS, dC, nu + extra_nu, delta, **kw)[0]


# ----------------------------------------------------------------- select_round --

LABELS = "ABCDEFGH"


@st.composite
def round_inputs(draw: st.DrawFn) -> tuple:
    grid = st.sampled_from([0.0, 0.25, 0.5, 0.55, 0.6, 0.7, 1.0])
    score = st.one_of(grid, unit)
    cost = st.one_of(st.none(), st.just(0.0), st.sampled_from([500.0, 1000.0, 1500.0]),
                     st.floats(1.0, 5000.0))
    inc = EvalResult("inc", 2, {}, draw(score), draw(cost), 4, 0)
    n = draw(st.integers(0, 8))
    cands = []
    for i in range(n):
        edits = [{"id": f"C{j}", "component": draw(st.sampled_from([*K, None]))}
                 for j in range(draw(st.integers(0, 3)))]
        if draw(st.booleans()) or i == 0:
            ev = EvalResult(f"c{i}", 2, {}, draw(score), draw(cost), 4, 0)
            cands.append(Candidate(LABELS[i], edits, ev))
        else:
            cands.append(Candidate(LABELS[i], edits,
                                   gate_failure=draw(st.sampled_from([None, "critic_reject"]))))
    S_star = min(1.0, inc.S + draw(st.sampled_from([0.0, 0.05, 0.2])))
    delta = draw(st.sampled_from([0.0, 0.02, 0.05]))
    counts = {c: draw(st.integers(0, 1)) for c in K}
    bad = {c.ev.job for c in cands if c.ev is not None and draw(st.booleans())}
    return inc, cands, S_star, delta, counts, bad


@settings(max_examples=500, deadline=None)
@given(inp=round_inputs(), tie=st.sampled_from(["paper", "xgen"]))
def test_select_round_winner_is_admissible_max(inp: tuple, tie: str) -> None:
    inc, cands, S_star, delta, counts, bad = inp
    cfg = RRSIParams(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)

    def guard(i: EvalResult, c: EvalResult) -> list[str]:
        return ["guard"] if c.job in bad else []

    winner, ds = select_round(cands, inc, S_star, delta, cfg, counts, guard, tie=tie)
    adm = [(c, d) for c, d in zip(cands, ds) if d.admissible]
    if not adm:
        assert winner is None
        return
    assert winner is not None
    wd = ds[next(i for i, c in enumerate(cands) if c is winner)]
    assert wd.admissible
    assert wd.S == max(d.S for _, d in adm)
    assert floor_ok(wd.S, S_star, delta)
    assert update_s_star(S_star, wd.S) >= S_star
    if tie == "paper":       # first admissible maximum
        assert winner is next(c for c, d in adm if d.S == wd.S)


# -------------------------------------------------------------------- prune_set --

record = st.builds(
    lambda t, comp, dS, acc: EditRecord(t, "A", "C1", comp, "h", dS, 0.0, acc,
                                        "ACCEPTED" if acc else "REJECTED"),
    st.integers(0, 10), st.sampled_from([*K, None, "bogus"]),
    st.one_of(st.none(), small), st.booleans())


@settings(max_examples=500, deadline=None)
@given(recs=st.lists(record, max_size=15), t_new=st.integers(0, 12),
       comp=st.sampled_from(K), dS=st.floats(-1.0, 0.0), t=st.integers(0, 12),
       n_prune=st.integers(0, 6))
def test_prune_set_monotone_under_nonpositive_records(recs: list, t_new: int, comp: str,
                                                      dS: float, t: int, n_prune: int) -> None:
    before = {p.component for p in prune_set(recs, t, n_prune)}
    added = recs + [EditRecord(t_new, "B", "C1", comp, "h", dS, 0.0, False, "REJECTED")]
    after = {p.component for p in prune_set(added, t, n_prune)}
    assert before <= after


# ------------------------------------------------- exact early stop (33 §4) --


def _check_stop_case(n_tasks: int, k: int, weights: list[float], inc_rewards: list[list[float]],
                     S_star_extra: float, delta: float, cand_rewards: list[list[float]],
                     order: list[tuple[int, int]], fill: list[float], other_S: float,
                     comp: str, other_comp: str, prior: list[tuple[int, str, float]]) -> bool:
    """Port of docs/research/verification/verify_bound.py onto our functions.

    Returns True when the evaluation stopped early (and every invariant held)."""
    cfg = RRSIParams(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)
    tok = [1000.0] * k

    def ev(job: str, rewards: list[list[float]]) -> EvalResult:
        return aggregate({f"t{i}": TaskResult(rewards[i], [weights[i]] * k, tok)
                          for i in range(n_tasks)}, k, job=job)

    inc = ev("inc", inc_rewards)
    S_star = min(1.0, inc.S + S_star_extra)
    A = B = 0.0
    R = sum(weights[i] for i in range(n_tasks)) * k
    stop_at = None
    for idx, (i, j) in enumerate(order):
        w = weights[i]
        A += cand_rewards[i][j] * w
        B += w
        R = max(0.0, R - w)
        if can_stop_exactly(A, B, R, S_star, delta, inc.S):
            stop_at = idx
            break
    if stop_at is None:
        return False
    # the remaining trials are filled arbitrarily
    full = [list(r) for r in cand_rewards]
    for (i, j), r in zip(order[stop_at + 1:], fill):
        full[i][j] = r
    full_ev = ev("cand", full)
    upper, lower = upper_lower(A, B, R)
    assert lower - 1e-12 <= full_ev.S <= upper + 1e-12   # summation order differs by ulps
    assert not floor_ok(full_ev.S, S_star, delta)
    partial_ev = EvalResult("cand", k, {}, upper, 1000.0, n_tasks * k, 0)
    other = Candidate("A", [{"id": "C1", "component": other_comp}], ev("other", [
        [other_S] * k for _ in range(n_tasks)]))
    candF = Candidate("B", [{"id": "C1", "component": comp}], full_ev)
    candE = Candidate("B", [{"id": "C1", "component": comp}], partial_ev)
    for tie in ("paper", "xgen"):
        wF, dF = select_round([other, candF], inc, S_star, delta, cfg, {}, tie=tie)
        wE, dE = select_round([other, candE], inc, S_star, delta, cfg, {}, tie=tie)
        assert (wF.variant if wF else None) == (wE.variant if wE else None)
        assert dF[0] == dE[0]
        assert dF[1].reason_code == dE[1].reason_code == "floor"
        S_next = wF.ev.S if wF and wF.ev else inc.S
        assert update_s_star(S_star, S_next) == update_s_star(
            S_star, wE.ev.S if wE and wE.ev else inc.S)
    # history: prior records + this candidate's record (full ΔS vs the upper bound)
    hist: list[EditRecord] = []
    for t_i, c_i, d_i in prior:
        hist += edit_records(t_i, "Z", [{"id": "C1", "component": c_i, "hypothesis": "h"}],
                             "REJECTED", d_i, 0.0, False, 0.5, 1000.0)
    t = 6
    dS_full = full_ev.S - inc.S
    dS_bound = early_stop_record_delta(A, B, R, inc.S)
    assert dS_full <= dS_bound + 1e-12 and dS_bound < 0
    edit = [{"id": "C1", "component": comp, "hypothesis": "x"}]
    hF = hist + list(edit_records(t, "B", edit, "REJECTED", dS_full, 0.0, False,
                                  full_ev.S, 1000.0))
    hE = hist + list(edit_records(t, "B", edit, "REJECTED", dS_bound, 0.0, False, upper,
                                  1000.0, early_stopped=True))
    for tt in range(t, t + 6):
        for npr in (2, 4):
            assert {p.component for p in prune_set(hF, tt, npr)} == \
                {p.component for p in prune_set(hE, tt, npr)}
    assert tried(hF) == tried(hE)
    assert accepted_counts(hF) == accepted_counts(hE)
    for stall in (0, 1):
        assert exploration(stall, tried(hF), 1) == exploration(stall, tried(hE), 1)
    return True


@st.composite
def stop_cases(draw: st.DrawFn) -> tuple:
    n_tasks = draw(st.integers(3, 12))
    k = draw(st.integers(1, 3))
    weights = draw(st.lists(st.sampled_from([1.0, 0.5, 2.0, 3.0]), min_size=n_tasks,
                            max_size=n_tasks))
    reward = st.one_of(st.sampled_from([0.0, 1.0]), unit)
    inc_rewards = [draw(st.lists(reward, min_size=k, max_size=k)) for _ in range(n_tasks)]
    low = st.one_of(st.just(0.0), st.sampled_from([0.0, 0.0, 0.5, 1.0]), unit)
    cand_rewards = [draw(st.lists(low, min_size=k, max_size=k)) for _ in range(n_tasks)]
    order = draw(st.permutations([(i, j) for i in range(n_tasks) for j in range(k)]))
    fill = draw(st.lists(reward, min_size=n_tasks * k, max_size=n_tasks * k))
    prior = draw(st.lists(st.tuples(st.integers(0, 5), st.sampled_from(K),
                                    st.floats(-0.1, 0.1)), max_size=6))
    return (n_tasks, k, weights, inc_rewards,
            draw(st.sampled_from([0.0, 0.1, 0.3])), draw(st.sampled_from([0.0, 0.02, 0.05, 0.1])),
            cand_rewards, list(order), fill, draw(unit), draw(st.sampled_from(K)),
            draw(st.sampled_from(K)), prior)


@settings(max_examples=500, deadline=None)
@given(case=stop_cases())
def test_can_stop_exactly_preserves_decisions(case: tuple) -> None:
    event("stopped early" if _check_stop_case(*case) else "full evaluation")


def test_can_stop_exactly_randomized_like_verify_bound() -> None:
    """The 4,000-trial loop of verify_bound.py with our functions (seeded, deterministic)."""
    rng = random.Random(0)
    stopped = 0
    for _ in range(4000):
        n_tasks, k = rng.randint(3, 12), rng.randint(1, 3)
        inc_p, p = rng.random(), rng.random()
        inc_rewards = [[1.0 if rng.random() < inc_p else 0.0 for _ in range(k)]
                       for _ in range(n_tasks)]
        cand = [[1.0 if rng.random() < p else 0.0 for _ in range(k)] for _ in range(n_tasks)]
        order = [(i, j) for i in range(n_tasks) for j in range(k)]
        fill = [1.0 if rng.random() < p else 0.0 for _ in order]
        prior = [(rng.randint(0, 5), rng.choice(K), rng.uniform(-0.1, 0.1))
                 for _ in range(rng.randint(0, 6))]
        stopped += _check_stop_case(
            n_tasks, k, [1.0] * n_tasks, inc_rewards, rng.choice([0.0, rng.random() * 0.3]),
            rng.choice([0.0, 0.02, 0.05, 0.1]), cand, order, fill, rng.random(),
            rng.choice(K), rng.choice(K), prior)
    assert stopped > 1000


# ------------------------------------------------------------------------ Dream --


@settings(max_examples=500, deadline=None)
@given(V=st.lists(st.floats(-10, 10), min_size=1, max_size=8),
       tie=st.sampled_from(["paper", "xgen"]), tol=st.sampled_from([0.0, 0.01, 0.5]))
def test_select_policy_never_worse_than_current(V: list[float], tie: str, tol: float) -> None:
    m = select_policy(V, tie=tie, tol=tol)
    assert V[m] >= V[0]
    assert_non_decreasing(V, m)
    if tie == "paper" or tol == 0.0:
        assert m == V.index(max(V))


@settings(max_examples=500, deadline=None)
@given(W=st.integers(1, 8), data=st.data())
def test_parallel_penalty_bounds(W: int, data: st.DataObject) -> None:
    batches = data.draw(st.lists(st.integers(1, W), min_size=1, max_size=10))
    N = sum(batches)
    ep = ReplayEpisode("w", 0.5, tuple(batches), tuple([0.5] * N), 0.4, N, len(batches), W, N)
    pen = episode_penalty(ep)
    assert pen == len(batches) / N                       # = k★ / N when |C| <= W
    assert 1 / W - 1e-12 <= pen <= 1.0
    assert 1 / W - 1e-12 <= parallel_penalty([ep, ep]) <= 1.0


point = st.tuples(unit, unit)


@settings(max_examples=500, deadline=None)
@given(worlds=st.dictionaries(st.sampled_from(["w1", "w2", "w3"]),
                              st.lists(point, max_size=6), max_size=3),
       extra=point, world=st.sampled_from(["w1", "w2", "w3"]))
def test_pareto_auc_bounded_and_monotone(worlds: dict, extra: tuple, world: str) -> None:
    auc = pareto_auc_v1(worlds)
    assert -1e-12 <= auc <= 1.0 + 1e-12
    if world in worlds:
        more = dict(worlds)
        more[world] = [*worlds[world], extra]
        assert pareto_auc_v1(more) >= auc - 1e-12


@settings(max_examples=300, deadline=None)
@given(scores=st.lists(st.floats(-5, 5), max_size=8), base=st.floats(-5, 5),
       s=st.floats(-10, 10))
def test_normalizer_range(scores: list[float], base: float, s: float) -> None:
    norm = make_normalizer(scores, base)
    assert 0.0 <= norm(s) <= 1.0
    assert make_normalizer(scores, base, "paper")(s) == s
    ceil = max(scores, default=base)
    if ceil > base and s >= ceil:
        assert norm(s) == 1.0
    if s < base:
        assert norm(s) == 0.0


def test_normalizer_monotone() -> None:
    norm = make_normalizer([0.1, 0.9, 0.4], 0.2)
    xs = [i / 20 for i in range(-5, 30)]
    ys = [norm(x) for x in xs]
    assert all(a <= b for a, b in zip(ys, ys[1:]))
    assert math.isclose(norm(0.55), 0.5)
