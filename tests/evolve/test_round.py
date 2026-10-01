"""End-to-end RRSI rounds: scripted role LLMs + a scripted policy whose behaviour depends on the
harness, real git worktrees, the real kernel and the real evaluation runner."""

from __future__ import annotations

import json

import pytest

from tests.evolve.support import (
    BAD,
    EVOLVE_IDS,
    GOOD,
    accept_verdicts,
    analyst_script,
    block_edit,
    digester_script,
    edit_decl,
    j,
    make_config,
    make_domain,
    make_roles,
    make_run,
    no_proposal_script,
    proposer_script,
    read_jsonl,
)
from xgen_rsi.evolve.driver import drive
from xgen_rsi.evolve.round import EvolveError, EvolveRun, NeedsReevaluation

DECISION_KEYS = {"variant", "admissible", "reason", "reason_code", "S", "C", "delta_S", "delta_C",
                 "novelty", "guards", "outcome", "commit", "early_stopped", "delta_C_partial"}
HISTORY_KEYS = {"t", "variant", "edit_id", "component", "hypothesis", "targets_mode",
                "predicted_affected", "delta_S", "delta_C", "accepted", "outcome", "S", "C",
                "bundle", "detail", "early_stopped", "diff", "ts"}


def _roles_for(proposer, critic=None):
    return make_roles(proposer=proposer, critic=critic if critic is not None else accept_verdicts(),
                      analyst=analyst_script(), digester=digester_script())


def test_improving_candidate_is_accepted_and_files_are_well_formed(tmp_path):
    roles = _roles_for(proposer_script(GOOD))
    run = make_run(tmp_path, roles=roles)
    base = run.baseline()
    assert base.S == pytest.approx(0.5) and base.missing == 0

    res = run.round(0)
    assert res["winner"] == "A"

    fr = run.frontier()
    assert set(fr) >= {"name", "incumbent", "S_star", "trajectory", "config"}
    inc = fr["incumbent"]
    assert set(inc) >= {"t", "commit", "harness_tree", "harness_version", "job", "S", "C", "extra"}
    assert inc["t"] == 1 and inc["job"] == "r0A" and inc["S"] == pytest.approx(1.0)
    assert inc["harness_version"].startswith("sha256:")
    assert fr["S_star"] == pytest.approx(1.0)
    assert [e["t"] for e in fr["trajectory"]] == [0, 1]
    assert {"t", "S", "C", "commit", "job"} <= set(fr["trajectory"][1])
    assert "api_key" not in json.dumps(fr["config"])
    # the branch moved to the winner and the incumbent tree is the winner's
    assert run.repo.rev(run.branch) == inc["commit"]
    assert run.repo.tree_hash(run.branch) == inc["harness_tree"]

    dec = json.loads((run.run_dir / "r0" / "decisions.json").read_text())
    assert dec["winner"] == "A" and dec["readjudicated"] is False
    assert set(dec["inputs"]) >= {"S_star", "delta", "incumbent", "accepted_counts", "params", "tie", "enabled_kinds"}
    row = dec["decisions"][0]
    assert DECISION_KEYS <= set(row)
    assert row["admissible"] and row["reason_code"] == "admissible" and row["outcome"] == "ACCEPTED"
    assert row["delta_S"] == pytest.approx(0.5) and row["delta_C"] == pytest.approx(0.0)
    assert "subagent" not in dec["inputs"]["enabled_kinds"]

    hist = read_jsonl(run.history.path)
    assert hist[0]["outcome"] == "BASELINE"
    rec = hist[1]
    assert HISTORY_KEYS <= set(rec)
    assert rec["component"] == "prompt" and rec["accepted"] is True and rec["outcome"] == "ACCEPTED"
    assert rec["delta_S"] == pytest.approx(0.5) and rec["bundle"] == 1 and rec["early_stopped"] is False

    att = read_jsonl(run.attribution_path)
    assert att[0]["predicted_hit"] == [EVOLVE_IDS[0]] and att[0]["hit_rate"] == 1.0

    directives = json.loads((run.run_dir / "r0" / "directives.json").read_text())
    assert directives["b_t"] == 2 and directives["sigma_t"] == 0 and directives["reserved_variants"] == []
    rdir = run.run_dir / "r0" / "A"
    for name in ("proposal.json", "critic.json", "critic_a0.json", "diff.patch", "prep.json", "smoke.json", "eval.json", "tags.json"):
        assert (rdir / name).exists(), name
    assert json.loads((rdir / "smoke.json").read_text())["ok"] is True
    tags = json.loads((rdir / "tags.json").read_text())
    assert tags["kinds"] == ["prompt"] and tags["touches"][0]["address"] == "prompt.system.params.extra_blocks"
    # the analyst read rendered traces; the proposer prompt carried the constitution as cache prefix
    assert (run.run_dir / "r0" / "analysis" / "rendered" / f"{EVOLVE_IDS[0]}.txt").exists()
    assert "Proposer Constitution" in roles.proposer.calls[0]["cache_prefix"]
    assert "AT MOST 2 independent edit" in roles.proposer.calls[0]["prompt"]
    # all scripted responses consumed exactly
    assert not roles.proposer.responses and not roles.critic.responses
    assert not roles.analyst.responses and not roles.digester.responses

    # Algorithm 2 re-run on the stored measurements reproduces every decision
    before = {k: row[k] for k in ("admissible", "reason_code", "S", "C", "delta_S", "delta_C", "novelty")}
    fr_before = run.frontier()
    again = run.readjudicate(0)
    dec2 = json.loads((run.run_dir / "r0" / "decisions.json").read_text())
    assert again["winner"] == "A" and dec2["readjudicated"] is True
    assert {k: dec2["decisions"][0][k] for k in before} == before
    fr_after = run.frontier()
    assert fr_after["incumbent"]["commit"] == fr_before["incumbent"]["commit"]
    assert fr_after["S_star"] == fr_before["S_star"]
    assert [x["S"] for x in fr_after["trajectory"]] == [x["S"] for x in fr_before["trajectory"]]
    hist2 = [r for r in read_jsonl(run.history.path) if r.get("edit_id")]
    assert [(r["outcome"], r["accepted"], r["delta_S"]) for r in hist2] == [("ACCEPTED", True, 0.5)]
    assert hist2[0]["detail"].startswith("[re-adjudicated delta=0.05000]")


@pytest.mark.parametrize("early_stop", [False, True])
def test_worse_candidate_is_rejected_by_the_floor(tmp_path, early_stop):
    roles = _roles_for(proposer_script(BAD))
    run = make_run(tmp_path, roles=roles, cfg=make_config(early_stop=early_stop))
    run.baseline()
    res = run.round(0)
    assert res["winner"] is None
    dec = json.loads((run.run_dir / "r0" / "decisions.json").read_text())["decisions"][0]
    assert dec["admissible"] is False and dec["reason_code"] == "floor" and dec["outcome"] == "REJECTED"
    rec = [r for r in read_jsonl(run.history.path) if r.get("edit_id")][0]
    assert rec["outcome"] == "REJECTED" and rec["accepted"] is False
    fr = run.frontier()
    assert fr["incumbent"]["t"] == 0 and [x["S"] for x in fr["trajectory"]] == [0.5, 0.5]
    assert run.repo.rev(run.branch) == fr["trajectory"][0]["commit"]
    if not early_stop:
        assert dec["early_stopped"] is False and dec["delta_S"] == pytest.approx(-0.5)
        return
    # 3 tasks x k=2 x weight 2 = 12; after 4 zero trials Ŝ_max = 4/12 < 0.45 -> exact stop
    assert dec["early_stopped"] is True and dec["delta_C_partial"] is True
    assert dec["trials_run"] == 4
    assert dec["early_stop"]["S_upper"] == pytest.approx(1 / 3)
    assert dec["delta_S"] == pytest.approx(1 / 3 - 0.5)
    assert rec["early_stopped"] is True and rec["delta_C_partial"] is True
    assert rec["delta_S"] == pytest.approx(round(1 / 3 - 0.5, 6))
    assert read_jsonl(run.attribution_path)[0]["partial"] is True
    # unchanged delta: re-adjudication reproduces the early-stop decision
    run.readjudicate(0)
    dec2 = json.loads((run.run_dir / "r0" / "decisions.json").read_text())["decisions"][0]
    assert dec2["reason_code"] == "floor" and dec2["early_stopped"] is True
    # a wider noise band breaks the stop condition: never guessed, re-evaluation demanded
    wide = EvolveRun(run.domain, run.cfg.with_overrides(delta=0.6), run.run_dir, roles=make_roles(), log=lambda s: None)
    with pytest.raises(NeedsReevaluation) as err:
        wide.readjudicate(0)
    assert err.value.variants == ["A"]
    assert json.loads((run.run_dir / "r0" / "reevaluate_needed.json").read_text())["variants"] == ["A"]
    out = wide.reevaluate(0, ["A"])
    dec3 = json.loads((run.run_dir / "r0" / "decisions.json").read_text())["decisions"][0]
    assert out["winner"] is None and dec3["early_stopped"] is False and dec3["trials_run"] == 6
    assert dec3["reason_code"] == "cost_rule" and dec3["delta_S"] == pytest.approx(-0.5)
    assert not (run.run_dir / "r0" / "reevaluate_needed.json").exists()


def test_critic_rejection_is_repaired(tmp_path):
    leaked = "End the report with STATUS: DONE."  # a verifier check value: caught by the precheck
    proposer = [j(block_edit(leaked)),
                j({"action": "done", "summary": "status line", "edits": [edit_decl()]}),
                # repair round
                j({"action": "edit_file", "path": "manifest.json", "old": leaked, "new": GOOD}),
                j({"action": "done", "summary": "read back the deliverable once", "edits": [edit_decl()]})]
    roles = _roles_for(proposer, critic=accept_verdicts(1))
    run = make_run(tmp_path, roles=roles)
    run.baseline()
    res = run.round(0)
    rdir = run.run_dir / "r0" / "A"
    a0 = json.loads((rdir / "critic_a0.json").read_text())
    a1 = json.loads((rdir / "critic_a1.json").read_text())
    assert a0["verdict"] == "reject" and "precheck" in a0["reasons"][0] and "verifier check value" in a0["reasons"][0]
    assert a1["verdict"] == "accept"
    assert (rdir / "proposal_r1.json").exists()
    assert len(roles.critic.calls) == 1  # the precheck rejection spent no LLM review
    assert "REVIEWER OBJECTIONS" in roles.proposer.calls[2]["prompt"]
    assert res["winner"] == "A"
    assert "STATUS: DONE" not in (rdir / "diff.patch").read_text()


def test_critic_rejection_without_repair_drops_the_candidate(tmp_path):
    proposer = [j(block_edit(GOOD)), j({"action": "done", "summary": "x", "edits": [edit_decl()]})] + \
               [j({"action": "abort"})] * 4
    reject = j({"verdict": "reject", "reasons": ["inert machinery"], "risk_notes": []})
    roles = _roles_for(proposer, critic=[reject])
    run = make_run(tmp_path, roles=roles, cfg=make_config(repair_rounds=1))
    run.baseline()
    res = run.round(0)
    assert res["winner"] is None
    rec = [r for r in read_jsonl(run.history.path) if r.get("edit_id")][0]
    assert rec["outcome"] == "critic_reject" and rec["delta_S"] is None
    prep = json.loads((run.run_dir / "r0" / "A" / "prep.json").read_text())
    assert prep["gate_failure"] == "critic_reject"
    assert not run.repo.branch_exists("tiny/r0A")
    assert run.history.tried() == set()  # unmeasured: not in T_t


def test_stall_reserves_exploration_slot_judged_by_touched_addresses(tmp_path):
    skill_md = ("---\nname: deliverable-readback\ndescription: Confirm a written deliverable once before "
                "finishing.\n---\n1. Read the file you wrote.\n2. Compare it with the request once.\n")
    add_component = {"action": "edit_file", "path": "manifest.json", "old": '"components": [',
                     "new": '"components": [\n    {"id": "skills.library", "kind": "skill", '
                            '"impl": "xgen_rsi.components.skills:SkillLibraryComponent", '
                            '"files": ["skills/deliverable-readback/SKILL.md"]},'}
    proposer = (proposer_script(BAD)                       # round 0, variant A: measured prompt edit
                + no_proposal_script()                     # round 0, variant B
                + no_proposal_script()                     # round 1, variant A (not reserved)
                + [j(block_edit(GOOD)),                    # round 1, variant B (reserved)
                   j({"action": "done", "summary": "skill", "edits": [edit_decl(component="skill")]}),
                   # repair: really add a skill component
                   j({"action": "write_file", "path": "skills/deliverable-readback/SKILL.md", "content": skill_md}),
                   j(add_component),
                   j({"action": "done", "summary": "skill", "edits": [edit_decl(component="skill")]})])
    roles = make_roles(proposer=proposer, critic=accept_verdicts(3),
                       analyst=analyst_script() * 2, digester=digester_script() * 2)
    run = make_run(tmp_path, roles=roles, cfg=make_config(m=2, w=1))
    run.baseline()
    assert run.round(0)["winner"] is None
    assert [x["S"] for x in run.frontier()["trajectory"]] == [0.5, 0.5]
    assert run.history.tried() == {"prompt"}  # the rejected edit was measured

    res = run.round(1)
    directives = json.loads((run.run_dir / "r1" / "directives.json").read_text())
    assert directives["sigma_t"] == 1 and directives["reserved_variants"] == ["B"]
    assert "subagent" not in directives["explore"]["untried"] and "prompt" not in directives["explore"]["untried"]
    b = run.run_dir / "r1" / "B"
    first = json.loads((b / "critic_a0.json").read_text())
    assert first["verdict"] == "reject" and "RESERVED EXPLORATION SLOT" in first["reasons"][0]
    assert any("declared 'skill' but the diff touches 'prompt'" in line for line in run.lines)
    assert json.loads((b / "critic_a1.json").read_text())["verdict"] == "accept"
    assert res["winner"] == "B"
    dec = json.loads((run.run_dir / "r1" / "decisions.json").read_text())
    by = {d["variant"]: d for d in dec["decisions"]}
    assert by["A"]["reason_code"] == "no_proposal" and by["B"]["novelty"] == 1
    rec = [r for r in read_jsonl(run.history.path) if r.get("t") == 1 and r.get("variant") == "B"][0]
    assert rec["component"] == "skill" and rec["outcome"] == "ACCEPTED"
    assert set(json.loads((b / "tags.json").read_text())["kinds"]) == {"prompt", "skill"}
    assert run.history.accepted_counts()["skill"] == 1


def test_resume_after_crash_reuses_everything(tmp_path, monkeypatch):
    domain = make_domain(tmp_path)
    roles = _roles_for(proposer_script(GOOD))
    run = make_run(tmp_path, roles=roles, domain=domain)
    run.baseline()
    calls = {"n": 0}
    original = EvolveRun._write_decisions

    def crash_once(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash after evaluation")
        return original(self, *a, **k)

    monkeypatch.setattr(EvolveRun, "_write_decisions", crash_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        run.round(0)
    assert (run.run_dir / "r0" / "A" / "eval.json").exists()
    assert run.frontier()["trajectory"][-1]["t"] == 0

    # a fresh process: every role script is EMPTY, so any LLM call would raise
    resumed = make_run(tmp_path, roles=make_roles(), domain=domain)
    res = resumed.round(0)
    assert res["winner"] == "A"
    assert any("resuming committed candidate" in line for line in resumed.lines)
    assert any("reusing eval.json" in line for line in resumed.lines)
    assert any("reusing analysis_report.json" in line for line in resumed.lines)
    recs = [r for r in read_jsonl(resumed.history.path) if r.get("edit_id")]
    assert len(recs) == 1 and recs[0]["outcome"] == "ACCEPTED"
    with pytest.raises(EvolveError, match="already settled"):
        resumed.round(0)


def test_reconcile_completes_an_interrupted_fast_forward_and_dry_run(tmp_path):
    roles = make_roles(proposer=proposer_script(GOOD), critic=accept_verdicts(),
                       analyst=analyst_script() * 2, digester=digester_script() * 2)
    run = make_run(tmp_path, roles=roles)
    run.baseline()
    run.round(0)
    fr = run.frontier()
    run.repo.update_ref(run.branch, fr["trajectory"][0]["commit"])  # crash before the ref moved
    run.reconcile()
    assert run.repo.rev(run.branch) == fr["incumbent"]["commit"]
    # dry run: analysis only (the next round starts from the reconciled incumbent)
    run.repo.update_ref(run.branch, fr["trajectory"][0]["commit"])
    assert run.round(1, dry_run=True) is None
    assert (run.run_dir / "r1" / "analysis_report.json").exists()
    assert not (run.run_dir / "r1" / "directives.json").exists()
    assert len(run.frontier()["trajectory"]) == 2


def test_paper_mode_uses_reference_tie_and_no_early_stop(tmp_path):
    cfg = make_config(mode="paper", early_stop=None)
    run = make_run(tmp_path, roles=_roles_for(proposer_script(GOOD)), cfg=cfg)
    run.baseline()
    assert run.round(0)["winner"] == "A"
    dec = json.loads((run.run_dir / "r0" / "decisions.json").read_text())
    directives = json.loads((run.run_dir / "r0" / "directives.json").read_text())
    assert dec["inputs"]["tie"] == "paper" and directives["early_stop"] is False
    # paper mode enables all of K, but the harness itself disables subagent (platform decision)
    assert directives["enabled_kinds"] == dec["inputs"]["enabled_kinds"] and "subagent" not in directives["enabled_kinds"]


def test_calibrate_heldout_and_driver(tmp_path):
    cfg = make_config(delta=None, T=2)
    roles = make_roles(proposer=proposer_script(GOOD) + no_proposal_script(), critic=accept_verdicts(),
                       analyst=analyst_script() * 2, digester=digester_script() * 2)
    run = make_run(tmp_path, roles=roles, cfg=cfg)
    out = drive(run, calibration_jobs=("base", "base_r2"))
    assert out["status"] == "done" and out["settled"] == 2 and not out["failures"]
    cal = json.loads(run.calibration_path.read_text())
    assert cal["jobs"] == ["base", "base_r2"] and cal["n_evals"] == 2
    assert cal["delta"] == 0.0  # the scripted policy is deterministic
    assert (run.jobs / "base_r2" / "eval.json").exists()
    assert run.frontier()["trajectory"][-1]["t"] == 2
    ev = run.heldout("h0", "heldout")
    assert ev.S == pytest.approx(1.0) and set(ev.per_task) == {"travel-claim-audit"}
    # STOP stops before the next round
    run.stop_path.write_text("")
    assert drive(run, T=3)["status"] == "stopped"


def test_round_requires_a_baseline_and_a_noise_band(tmp_path):
    run = make_run(tmp_path, cfg=make_config(delta=None))
    with pytest.raises(EvolveError, match="baseline"):
        run.round(0)
    run.baseline()
    with pytest.raises(EvolveError, match="noise band"):
        run.round(0)


class _Flaky:
    """Policy client factory that fails (infrastructure) for one evaluation job."""

    def __init__(self, job: str, fail_first: int = 10**9) -> None:
        self.job, self.fail_first, self.failed = job, fail_first, 0

    def __call__(self, plan):
        from tests.evolve.support import policy_factory

        if f"/jobs/{self.job}/" in plan.run_tool_context.working_dir and self.failed < self.fail_first:
            self.failed += 1
            raise ConnectionError("provider down")
        return policy_factory(plan)


def test_infrastructure_failure_is_eval_invalid_then_reevaluated(tmp_path):
    domain = make_domain(tmp_path)
    flaky = _Flaky("r0A")
    domain.client_factory = flaky
    run = make_run(tmp_path, roles=_roles_for(proposer_script(GOOD)), domain=domain)
    run.baseline()
    assert run.round(0)["winner"] is None
    assert flaky.failed == 12  # 6 trials, all missing, retried once
    rec = [r for r in read_jsonl(run.history.path) if r.get("edit_id")][0]
    assert rec["outcome"] == "eval_invalid" and rec["delta_S"] is None
    dec = json.loads((run.run_dir / "r0" / "decisions.json").read_text())["decisions"][0]
    assert dec["reason_code"] == "eval_invalid"
    assert run.history.tried() == set()
    # the infrastructure recovers: re-measure the committed candidate and re-adjudicate
    flaky.fail_first = 0
    out = run.reevaluate(0)
    assert out["winner"] == "A"
    assert run.frontier()["incumbent"]["job"] == "r0A" and run.repo.rev(run.branch) == run.frontier()["incumbent"]["commit"]
    recs = [r for r in read_jsonl(run.history.path) if r.get("edit_id")]
    assert [r["outcome"] for r in recs] == ["ACCEPTED"]


def test_early_stop_is_not_trusted_when_missing_trials_could_explain_it(tmp_path):
    domain = make_domain(tmp_path)
    domain.client_factory = _Flaky("r0A", fail_first=1)
    cfg = make_config(early_stop=True, invalid_missing_frac=0.2)
    run = make_run(tmp_path, roles=_roles_for(proposer_script(BAD)), cfg=cfg, domain=domain)
    run.baseline()
    run.round(0)
    dec = json.loads((run.run_dir / "r0" / "decisions.json").read_text())["decisions"][0]
    assert any("not robust to the missing trials" in line for line in run.lines)
    assert dec["early_stopped"] is False and dec["trials_run"] == 6 and dec["missing"] == 1
    assert dec["reason_code"] == "floor"


def test_export_writes_the_incumbent_harness_for_deployment(tmp_path):
    """rsi evolve export — 채택된 하네스를 디렉터리로 꺼내면 버전이 frontier 의 incumbent 와 같다."""
    from xgen_rsi.evolve.cli import export_harness
    from xgen_rsi.harness.spec import load_manifest

    run = make_run(tmp_path, roles=_roles_for(proposer_script(GOOD)))
    run.baseline()
    run.round(0)
    out = export_harness(run, str(tmp_path / "H_star"))
    inc = run.frontier()["incumbent"]
    assert out["version"] == inc["harness_version"] == load_manifest(tmp_path / "H_star").version_id()
    assert not (tmp_path / "H_star" / ".git").exists()


def test_relative_run_dir_from_the_cli_works(tmp_path, monkeypatch):
    """CLI 는 RUN 을 상대 경로로 넘긴다 — worktree 가 하네스 저장소 기준으로 풀리면 기준선이 manifest 를 못 찾았다."""
    monkeypatch.chdir(tmp_path)
    run = EvolveRun(make_domain(tmp_path), make_config(), "rel/run", roles=_roles_for(proposer_script(GOOD)), log=lambda s: None)
    assert run.run_dir.is_absolute()
    base = run.baseline()
    assert base.missing == 0 and not (tmp_path / "rel/run/harness_repo/rel").exists()
