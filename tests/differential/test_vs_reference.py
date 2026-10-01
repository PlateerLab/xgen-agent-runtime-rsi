"""Differential tests: `paper` mode against the official RRSI implementation.

Reference: google-research/rrsi @ be50316 (installed as the `rrsi` package, test
dependency only). Every comparison is exact (==), including reason strings.
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import rrsi.calibrate as RC
import rrsi.components as RCO
import rrsi.evaluate as RE
import rrsi.history as RH
import rrsi.schedule as RS
import rrsi.selection as RSEL
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rrsi.config import RRSIConfig
from rrsi.loop import Run

from xgen_rsi.rsi_math import (
    Candidate,
    EditRecord,
    EvalResult,
    K,
    RRSIParams,
    TaskResult,
    accepted_counts,
    aggregate,
    attribute,
    bootstrap_se,
    calibrate,
    classify_diff,
    cost_rule,
    edit_budget,
    edit_records,
    exploration,
    has_evidence,
    judge,
    measured,
    normalize_component,
    novelty,
    outcome_of,
    pooled,
    prune_set,
    recent_yield,
    relative_cost_change,
    select_round,
    stall_flag,
    text_only,
    tried,
    valid_measurement,
)


def fixture_settings(n: int) -> settings:
    """Settings for tests that also take pytest's function-scoped tmp_path."""
    return settings(max_examples=n, deadline=None,
                    suppress_health_check=[HealthCheck.function_scoped_fixture])


unit = st.floats(0.0, 1.0, allow_nan=False)
# The reference History reads JSONL with str.splitlines(), which also splits on these
# characters while append() writes them raw (ensure_ascii=False): a reference I/O quirk,
# not a formula, so generated text avoids them.
LINE_BREAKS = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
safe_text = st.characters(blacklist_categories=("Cs",), blacklist_characters=LINE_BREAKS)

# ----------------------------------------------------------------- conversions --


def ref_task(tr: TaskResult) -> RE.TaskResult:
    return RE.TaskResult(rewards=list(tr.rewards), weights=list(tr.weights),
                         tokens=list(tr.tokens), missing=tr.missing, extra=dict(tr.extra))


def ref_eval(ev: EvalResult) -> RE.EvalResult:
    return RE.EvalResult(job=ev.job, k=ev.k,
                         per_task={t: ref_task(r) for t, r in ev.per_task.items()},
                         S=ev.S, C=ev.C, n_expected=ev.n_expected, missing=ev.missing,
                         extra=dict(ev.extra))


def ref_cfg(p: RRSIParams) -> RRSIConfig:
    return RRSIConfig(beta0=p.beta0, beta1=p.beta1, w_s=p.w_s, w_c=p.w_c, w_n=p.w_n)


# ------------------------------------------------------------------- strategies --

token = st.one_of(st.none(), st.just(0), st.integers(-5, 10**6),
                  st.floats(-10.0, 1e6, allow_nan=False))


@st.composite
def task_result(draw: st.DrawFn, k: int) -> TaskResult:
    n = draw(st.integers(0, k))
    rewards = draw(st.lists(st.one_of(unit, st.sampled_from([0.0, 1.0])), min_size=n,
                            max_size=n))
    weights = draw(st.one_of(
        st.just([]),
        st.lists(st.one_of(st.floats(0.001, 100.0), st.sampled_from([0.0, 1.0, 3.0])),
                 min_size=n, max_size=n)))
    tokens = draw(st.one_of(st.just([]), st.lists(token, min_size=n, max_size=n)))
    if not rewards:
        weights, tokens = [], []
    return TaskResult(rewards, weights, tokens, missing=draw(st.integers(0, k)))


@st.composite
def per_task_and_k(draw: st.DrawFn, max_tasks: int = 50) -> tuple[dict[str, TaskResult], int]:
    k = draw(st.integers(1, 5))
    n = draw(st.integers(1, max_tasks))
    return {f"x{i}": draw(task_result(k)) for i in range(n)}, k


# ----------------------------------------------------------------- edit_budget --


@settings(max_examples=3000, deadline=None)
@given(T=st.integers(-2, 200), b=st.tuples(st.integers(1, 10), st.integers(1, 10)),
       dt=st.integers(-5, 205))
def test_edit_budget(T: int, b: tuple[int, int], dt: int) -> None:
    b_min, b_max = min(b), max(b)
    t = min(dt, T + 5)
    assert edit_budget(t, T, b_min, b_max) == RS.edit_budget(t, T, b_min, b_max)


def test_edit_budget_full_grid() -> None:
    for T in range(1, 61):
        for b_min, b_max in itertools.combinations_with_replacement(range(1, 8), 2):
            for t in range(-2, T + 3):
                assert edit_budget(t, T, b_min, b_max) == RS.edit_budget(t, T, b_min, b_max)


# ------------------------------------------------------------------- aggregate --


@settings(max_examples=600, deadline=None)
@given(inp=per_task_and_k())
def test_aggregate(inp: tuple[dict[str, TaskResult], int]) -> None:
    per, k = inp
    ours = aggregate(per, k, job="j", extra={"x": 1})
    ref = RE.aggregate("j", k, {t: ref_task(r) for t, r in per.items()}, {"x": 1})
    assert (ours.S, ours.C, ours.n_expected, ours.missing, ours.job, ours.k) == \
        (ref.S, ref.C, ref.n_expected, ref.missing, ref.job, ref.k)
    assert ours.to_json() == ref.to_json()
    assert EvalResult.from_json(ref.to_json()) == ours
    assert {t: ours.per_task[t].mean for t in per} == {t: ref.per_task[t].mean for t in per}


cost = st.one_of(st.none(), st.just(0), st.just(0.0), st.integers(-10, 10**5),
                 st.floats(-1e6, 1e6, allow_nan=False))


@settings(max_examples=1000, deadline=None)
@given(c1=cost, c2=cost)
def test_relative_cost_change(c1: float | None, c2: float | None) -> None:
    assert relative_cost_change(c1, c2) == RE.relative_cost_change(c1, c2)


# ------------------------------------------------------------------- cost rule --

params = st.builds(RRSIParams, beta0=st.floats(0, 1), beta1=st.floats(0, 100),
                   w_s=st.one_of(st.just(0.0), st.floats(0, 2000)),
                   w_c=st.floats(0, 20), w_n=st.one_of(st.just(0.0), st.floats(0, 2)))


@settings(max_examples=1500, deadline=None)
@given(dS=st.floats(-1, 1), dC=st.floats(-1, 60), nu=st.integers(0, 4),
       delta=st.one_of(st.just(0.0), st.floats(0, 0.2)), p=params,
       boundary=st.sampled_from([None, "band_edge", "shaped_zero", "eq7_edge"]))
def test_cost_rule(dS: float, dC: float, nu: int, delta: float, p: RRSIParams,
                   boundary: str | None) -> None:
    if boundary == "band_edge":
        dS = delta                                   # ΔS = δ: inside the band
    elif boundary == "shaped_zero":
        dS, dC, nu = 0.0, 0.0, 0                     # Eq.17 value exactly 0
    elif boundary == "eq7_edge":
        dS = delta + 0.01
        dC = p.beta0 + p.beta1 * dS                  # Eq.7 equality
    ours = cost_rule(dS, dC, nu, delta, beta0=p.beta0, beta1=p.beta1, w_s=p.w_s, w_c=p.w_c,
                     w_n=p.w_n)
    assert ours == RSEL.cost_rule(dS, dC, nu, delta, ref_cfg(p))


# ------------------------------------------------------------ judge / select --

LABELS = "ABCDEFGH"
component = st.sampled_from([*K, None, "", "bogus"])


@st.composite
def selection_inputs(draw: st.DrawFn) -> dict[str, Any]:
    grid = st.sampled_from([0.0, 0.3, 0.5, 0.52, 0.55, 0.6, 0.7, 1.0])
    score = st.one_of(grid, unit)
    c = st.one_of(st.none(), st.just(0.0), st.sampled_from([800.0, 1000.0, 1200.0]),
                  st.floats(1.0, 1e5))
    inc = EvalResult("inc", 2, {}, draw(score), draw(c), 4, 0)
    cands, bad = [], set()
    for i in range(draw(st.integers(0, 8))):
        edits = [{"id": f"C{j}", "component": draw(component), "hypothesis": "h"}
                 for j in range(draw(st.integers(0, 3)))]
        gate = draw(st.sampled_from([None, None, "critic_reject", "smoke_fail", "eval_invalid"]))
        ev = (EvalResult(f"c{i}", 2, {}, draw(score), draw(c), 4, 0)
              if gate is None or draw(st.booleans()) else None)
        if ev is not None and draw(st.integers(0, 4)) == 0:
            bad.add(ev.job)
        cands.append(Candidate(LABELS[i], edits, ev, gate_failure=gate))
    return {"inc": inc, "cands": cands, "bad": bad,
            "S_star": min(1.0, inc.S + draw(st.sampled_from([0.0, 0.02, 0.05, 0.2]))),
            "delta": draw(st.one_of(st.sampled_from([0.0, 0.02, 0.05]), st.floats(0, 0.2))),
            "counts": {k: draw(st.integers(0, 2)) for k in K if draw(st.booleans())},
            "cfg": draw(params)}


def _decision_tuple(d: Any) -> tuple:
    return (d.variant, d.admissible, d.reason, d.S, d.C, d.delta_S, d.delta_C, d.novelty,
            list(d.guards))


@settings(max_examples=800, deadline=None)
@given(inp=selection_inputs())
def test_judge_and_select_round(inp: dict[str, Any]) -> None:
    inc, cands, bad = inp["inc"], inp["cands"], inp["bad"]
    cfg, S_star, delta, counts = inp["cfg"], inp["S_star"], inp["delta"], inp["counts"]
    ref_cands = [RSEL.Candidate(c.variant, [dict(e) for e in c.edits],
                                ev=ref_eval(c.ev) if c.ev is not None else None,
                                gate_failure=c.gate_failure) for c in cands]
    ref_inc = ref_eval(inc)

    def g_ours(i: EvalResult, c: EvalResult) -> list[str]:
        return [f"violation {c.job}"] if c.job in bad else []

    def g_ref(i: RE.EvalResult, c: RE.EvalResult) -> list[str]:
        return [f"violation {c.job}"] if c.job in bad else []

    for guard_o, guard_r in ((None, None), (g_ours, g_ref)):
        w, ds = select_round(cands, inc, S_star, delta, cfg, counts, guard_o, tie="paper")
        rw, rds = RSEL.select_round(ref_cands, ref_inc, S_star, delta, ref_cfg(cfg), counts,
                                    guard_fn=guard_r)
        assert [_decision_tuple(d) for d in ds] == [_decision_tuple(d) for d in rds]
        w_idx = next((i for i, c in enumerate(cands) if c is w), None)
        rw_idx = next((i for i, c in enumerate(ref_cands) if c is rw), None)
        assert w_idx == rw_idx
        for c, rc, d, rd in zip(cands, ref_cands, ds, rds):
            ref_outcome = ((rc.gate_failure or "not_evaluated") if rc.ev is None else
                           "ACCEPTED" if rc is rw else ("LOST" if rd.admissible else "REJECTED"))
            assert outcome_of(c, w, d) == ref_outcome
            assert d.reason_code in {"floor", "cost_rule", "guard", "admissible",
                                     "not_evaluated", c.gate_failure}
        # single-candidate judge path, with explicit guard lists
        for c, rc in zip(cands, ref_cands):
            gl = ["x"] if (c.ev is not None and c.ev.job in bad) else []
            assert _decision_tuple(judge(c, inc, S_star, delta, cfg, counts, gl)) == \
                _decision_tuple(RSEL.judge(rc, ref_inc, S_star, delta, ref_cfg(cfg), counts, gl))


# -------------------------------------------------------- stall / exploration --


@settings(max_examples=1500, deadline=None)
@given(traj=st.lists(unit, max_size=12), t=st.integers(-3, 15), w=st.integers(0, 8),
       delta=st.one_of(st.just(0.0), st.floats(0, 0.2)))
def test_stall_flag(traj: list[float], t: int, w: int, delta: float) -> None:
    assert stall_flag(traj, t, w, delta) == RH.stall_flag(traj, t, w, delta)


@settings(max_examples=1000, deadline=None)
@given(stall=st.integers(0, 1), tried_set=st.sets(st.sampled_from([*K, "bogus"])),
       m_draft=st.integers(0, 3), t=st.integers(0, 20))
def test_exploration(stall: int, tried_set: set[str], m_draft: int, t: int) -> None:
    ours = exploration(stall, tried_set, m_draft)
    ref = RH.exploration(t, stall, tried_set, m_draft)
    assert (ours.sigma, list(ours.untried), ours.m_draft) == \
        (ref["sigma"], ref["untried"], ref["m_draft"])
    assert ours.reserved_active == ref["text"].startswith("STALL")


# ------------------------------------------------------------- history summaries --

record_dict = st.fixed_dictionaries({
    "t": st.integers(0, 10),
    "variant": st.sampled_from(["A", "B", "-"]),
    "edit_id": st.one_of(st.none(), st.sampled_from(["C1", "C2", "h1"])),
    "component": st.one_of(st.none(), st.sampled_from([*K, "bogus"])),
    "hypothesis": st.one_of(st.none(), st.text(safe_text, max_size=5)),
    "delta_S": st.one_of(st.none(), st.floats(-1, 1), st.just(0.0)),
    "delta_C": st.one_of(st.none(), st.floats(-1, 5)),
    "accepted": st.booleans(),
    "outcome": st.sampled_from(["ACCEPTED", "LOST", "REJECTED", "critic_reject",
                                "smoke_fail", "eval_invalid"]),
    "bundle": st.integers(0, 3),
})


@fixture_settings(600)
@given(recs=st.lists(record_dict, max_size=25), t=st.integers(0, 14),
       n_prune=st.integers(0, 6))
def test_history_summaries(tmp_path: Path, recs: list[dict], t: int, n_prune: int) -> None:
    path = tmp_path / "history.jsonl"
    path.unlink(missing_ok=True)
    hist = RH.History(path)
    for r in recs:
        hist.append(r)
    ours = [EditRecord.from_json(r) for r in hist.records()]
    assert len(measured(ours)) == len(hist.measured())
    assert tried(ours) == hist.tried()
    assert recent_yield(ours, t, n_prune) == hist.yield_g(t, n_prune)
    assert [p.to_json() for p in prune_set(ours, t, n_prune)] == hist.prune_set(t, n_prune)
    assert accepted_counts(ours) == hist.incumbent_component_counts()
    # re-adjudication count (rrsi/loop.py Run.readjudicate): accepted records with t_i < t
    counts = {c: 0 for c in hist.incumbent_component_counts()}
    for r in hist.records():
        if r.get("accepted") and r.get("t", 10**9) < t and r.get("component") in counts:
            counts[r["component"]] += 1
    assert accepted_counts(ours, before_t=t) == counts


edit_dict = st.fixed_dictionaries(
    {"id": st.sampled_from(["C1", "C2"]), "component": component},
    optional={"hypothesis": st.one_of(st.none(), st.text(safe_text, max_size=4)),
              "mechanism": st.text(safe_text, max_size=4),
              "targets_mode": st.sampled_from(["fix", "preserve"]),
              "predicted_affected": st.lists(st.sampled_from(["x0", "x1"]), max_size=2)})


@fixture_settings(400)
@given(t=st.integers(0, 9), edits=st.lists(edit_dict, max_size=3),
       outcome=st.sampled_from(["ACCEPTED", "LOST", "REJECTED", "critic_reject"]),
       dS=st.one_of(st.none(), st.floats(-1, 1)), dC=st.one_of(st.none(), st.floats(-1, 9)),
       S=st.one_of(st.none(), unit), C=st.one_of(st.none(), st.floats(0, 1e5)),
       accepted=st.booleans(), detail=st.text(safe_text, max_size=700))
def test_edit_records_match_history_append(tmp_path: Path, t: int, edits: list[dict],
                                           outcome: str, dS: float | None, dC: float | None,
                                           S: float | None, C: float | None, accepted: bool,
                                           detail: str) -> None:
    path = tmp_path / "h.jsonl"
    path.unlink(missing_ok=True)
    RH.History(path).append_candidate(t, "A", edits, outcome, dS, dC, accepted, S, C, None,
                                      detail)
    ref = RH.History(path).records()
    ours = edit_records(t, "A", edits, outcome, dS, dC, accepted, S, C, detail)
    keys = ("t", "variant", "edit_id", "component", "hypothesis", "targets_mode", "delta_S",
            "delta_C", "accepted", "outcome", "S", "C", "bundle", "detail")
    assert [{k: r.to_json()[k] for k in keys} for r in ours] == \
        [{k: r[k] for k in keys} for r in ref]
    assert [list(r.predicted_affected) for r in ours] == \
        [r["predicted_affected"] or [] for r in ref]
    assert [EditRecord.from_json(json.loads(json.dumps(r.to_json()))) for r in ours] == list(ours)


# ------------------------------------------------------------ novelty / normalize --


@settings(max_examples=1500, deadline=None)
@given(comps=st.lists(st.sampled_from([*K, "bogus"]), max_size=8),
       counts=st.dictionaries(st.sampled_from([*K, "bogus"]), st.integers(0, 2)))
def test_novelty(comps: list[str], counts: dict[str, int]) -> None:
    assert novelty(comps, counts) == RCO.novelty(comps, counts)


LINES = ["+x = 1", "-y = 2", '+"prompt text"', "+'abc'", '+f"fmt {x}"', "+b'raw'",
         "+# comment", "-   # removed comment", "+++ b/file.py", "--- a/file.py",
         "+self.mem = Memory(path)", "+x.remember(y)", "+_STATE_DIR = p",
         "+skills/foo/SKILL.md", "+SkillRegistry()", "+registry = ToolRegistry()",
         "+CLIENT_TOOLS = []", "+out = subcall(llm, brief)", "+sub_agent = True",
         " context line", "+", "-", "+CONFIG = 3", '+    """doc"""', "+print(x)",
         "+notes = []", "+    return value"]
SIGNALS = st.one_of(st.none(), st.just([]), st.just([("config", [r"CONFIG"])]),
                    st.just([("output_plumbing", [r"print\("]), ("memory", [r"notes"])]))
declared = st.one_of(st.none(), st.sampled_from(K),
                     st.sampled_from([" Skill ", "MEMORY", "bogus", "", "Prompt"]))


@settings(max_examples=2000, deadline=None)
@given(decl=declared, lines=st.lists(st.sampled_from(LINES), max_size=8), signals=SIGNALS)
def test_normalize_signal_path(decl: str | None, lines: list[str], signals: Any) -> None:
    diff = "\n".join(lines)
    assert text_only(diff) == RCO.text_only(diff)
    assert classify_diff(diff, signals) == RCO.classify_diff(diff, signals)
    for comp in K:
        assert has_evidence(comp, diff, signals) == RCO.has_evidence(comp, diff, signals)
    assert normalize_component(decl, set(), diff, signals) == \
        RCO.normalize(decl, diff, signals)


# -------------------------------------------------------------------- calibrate --


@st.composite
def base_evals(draw: st.DrawFn) -> list[EvalResult]:
    out = []
    for _ in range(draw(st.integers(1, 4))):
        k = draw(st.integers(1, 3))
        tasks = draw(st.lists(st.sampled_from(["t0", "t1", "t2", "t3", "t4"]), min_size=1,
                              max_size=5, unique=True))
        per = {}
        for t in tasks:
            n = draw(st.integers(0, k))
            rewards = draw(st.lists(st.one_of(st.sampled_from([0.0, 1.0]), unit),
                                    min_size=n, max_size=n))
            weights = draw(st.one_of(st.just([]), st.lists(st.floats(0.1, 10.0), min_size=n,
                                                           max_size=n)))
            per[t] = TaskResult(rewards, weights if rewards else [])
        out.append(aggregate(per, k, job="base"))
    return out


@settings(max_examples=300, deadline=None)
@given(evals=base_evals(), reps=st.sampled_from([1, 2, 50, 2000]),
       z=st.sampled_from([2.0, 1.0, 2.5]))
def test_calibrate(evals: list[EvalResult], reps: int, z: float) -> None:
    ref_evals = [ref_eval(e) for e in evals]
    ours = calibrate(evals, z=z, reps=reps)
    ref = RC.calibrate(ref_evals, z=z, reps=reps)
    assert ours.delta == ref["delta"]
    assert ours.to_json() == ref
    p, rp = pooled(evals), RC.pooled(ref_evals)
    assert (p.S, p.C, p.k, p.n_expected, p.missing) == (rp.S, rp.C, rp.k, rp.n_expected,
                                                        rp.missing)
    assert bootstrap_se(p, reps=reps) == RC.bootstrap_se(rp, reps=reps)


def test_calibrate_default_reps() -> None:
    evals = [aggregate({f"t{i}": TaskResult([float((i * j) % 2) for j in range(3)])
                        for i in range(8)}, 3, job="base")]
    ours, ref = calibrate(evals), RC.calibrate([ref_eval(e) for e in evals])
    assert ours.to_json() == ref and ours.delta > 0


# ------------------------------------------------------------------ attribution --


@fixture_settings(300)
@given(k=st.integers(1, 4), data=st.data())
def test_attribution(tmp_path: Path, k: int, data: st.DataObject) -> None:
    names = [f"x{i}" for i in range(data.draw(st.integers(1, 15)))]

    def ev(job: str) -> EvalResult:
        per = {n: TaskResult(data.draw(st.lists(st.sampled_from([0.0, 0.5, 1.0]), min_size=k,
                                                max_size=k)))
               for n in names if data.draw(st.integers(0, 9))}
        return aggregate(per, k, job=job)

    inc, cand = ev("inc"), ev("cand")
    edits = [{"id": "C1", "component": data.draw(component),
              "hypothesis": data.draw(st.one_of(st.none(), st.text(max_size=200))),
              "predicted_affected": data.draw(st.lists(st.sampled_from([*names, "zz", 3]),
                                                       max_size=5))}
             for _ in range(data.draw(st.integers(1, 3)))]
    path = tmp_path / "attribution.jsonl"
    path.unlink(missing_ok=True)
    fake = SimpleNamespace(domain=SimpleNamespace(regression_threshold=lambda kk: 1.0 / max(1, kk)),
                           cfg=SimpleNamespace(k=k), attribution_path=path)
    ref_rows = Run.attribute(fake, 3, "B", edits, ref_eval(inc), ref_eval(cand))
    ours = [attribute(e, inc, cand, k, t=3, variant="B").to_json() for e in edits]
    assert ours == ref_rows


# ------------------------------------------------------------ measurement gate --


@settings(max_examples=500, deadline=None)
@given(missing=st.integers(0, 60), n_tasks=st.integers(1, 20), k=st.integers(1, 3),
       frac=st.sampled_from([0.1, 0.15, 0.2, 0.0, 1.0]))
def test_valid_measurement(missing: int, n_tasks: int, k: int, frac: float) -> None:
    ev = EvalResult("j", k, {}, 0.5, None, n_tasks * k, missing)
    # rrsi/loop.py Run._evaluate: invalid iff missing > invalid_missing_frac * n_expected
    assert valid_measurement(ev, frac) == (not ev.missing > frac * ev.n_expected)
    assert math.isfinite(frac)
