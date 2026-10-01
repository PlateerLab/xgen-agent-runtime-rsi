"""Unit tests of the L1 building blocks: config, history, frontier, tagging, critic, render,
digester, analyst, gitops, domain patterns and the driver's failure handling."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.evolve.support import CHECKS, EVOLVE_IDS, GOOD, j, make_domain
from xgen_rsi.evolve import frontier as F
from xgen_rsi.evolve.analyst import analyze
from xgen_rsi.evolve.config import EvolveConfig
from xgen_rsi.evolve.critic import added_text, precheck, review
from xgen_rsi.evolve.digester import digest_task
from xgen_rsi.evolve.driver import drive
from xgen_rsi.evolve.gitops import AUTHOR_EMAIL, AUTHOR_NAME, HarnessRepo, diff_dirs, git
from xgen_rsi.evolve.history import History
from xgen_rsi.evolve.render import TrialView, cut_steps, render_trial, task_row
from xgen_rsi.evolve.tagging import normalize_edits, touched
from xgen_rsi.harness.spec import load_manifest
from xgen_rsi.kernel.executor import BUILTIN_H0
from xgen_rsi.roles.llm import RoleModel, ScriptedRoleLLM
from xgen_rsi.rsi_math import EvalResult, TaskResult

# ------------------------------------------------------------------ config --


def test_config_load_overrides_notes_and_redaction(tmp_path):
    p = tmp_path / "rrsi.json"
    p.write_text(json.dumps({
        "_doc": "instance notes", "T": 5, "k": 3, "delta": None, "beta1": 35.4, "m": 2,
        "repair_rounds": 2, "early_stop": True, "mode": "paper", "concurrency": 10,
        "roles": {"proposer": {"provider": "anthropic", "model": "claude-x", "api_key": "secret-key"}},
        "critic_model": {"provider": "openai", "model": "gpt-x"},
        "max_valid_rate_drop": 0.03}))
    cfg = EvolveConfig.load(p, k=4, eval_parallel=2)
    assert cfg.T == 5 and cfg.k == 4 and cfg.m == 2 and cfg.delta is None
    assert cfg.params.beta1 == 35.4 and cfg.repair_rounds == 2 and cfg.eval_parallel == 2
    assert cfg.mode == "paper" and cfg.use_early_stop is True and cfg.mode_config.select_tie == "paper"
    assert cfg.notes == {"_doc": "instance notes", "concurrency": 10}
    assert cfg.proposer == RoleModel(provider="anthropic", model="claude-x", api_key="secret-key")
    assert cfg.critic is not None and cfg.critic.model == "gpt-x"
    dumped = json.dumps(cfg.dump())
    assert "secret-key" not in dumped and "claude-x" in dumped
    assert cfg.with_overrides(delta=0.02, repair_rounds=1).delta == 0.02
    assert EvolveConfig().use_early_stop is True  # xgen default
    with pytest.raises(ValueError):
        EvolveConfig(mode="other")
    with pytest.raises(KeyError):
        cfg.with_overrides(nope=1)


# ----------------------------------------------------------------- history --


def test_history_jsonl_survives_unicode_line_separators(tmp_path):
    h = History(tmp_path / "history.jsonl")
    hyp = "first part second part\u0085third end — 한국어"
    h.append_candidate(0, "A", [{"id": "C1", "component": "prompt", "hypothesis": hyp,
                                 "declared_component": "memory"}],
                       "REJECTED", -0.01, 0.2, False, 0.49, 30.0, "d.patch", "floor")
    raw = h.path.read_text(encoding="utf-8")
    assert raw.isascii() and raw.count("\n") == 1
    rec = h.records()[0]
    assert rec["hypothesis"] == hyp and rec["declared_component"] == "memory"
    assert h.edit_records()[0].hypothesis == hyp


def test_history_summaries_follow_the_formula_library(tmp_path):
    # the 05 §8.5 vector, written through History
    h = History(tmp_path / "h.jsonl")
    h.append_candidate(0, "A", [{"id": "h1", "component": "prompt"}], "ACCEPTED", 0.03, 0.0, True, 0.6, 1.0, None)
    h.append_candidate(1, "A", [{"id": "h2", "component": "prompt"}], "REJECTED", -0.01, 0.0, False, 0.5, 1.0, None)
    h.append_candidate(1, "B", [{"id": "h3", "component": "skill"}, {"id": "h4", "component": "memory"}],
                       "REJECTED", -0.02, 0.0, False, 0.5, 1.0, None)
    h.append_candidate(2, "A", [{"id": "h5", "component": "config"}], "critic_reject", None, None, False, None, None, None)
    assert h.tried() == {"prompt", "skill", "memory"}
    g3 = h.recent_yield(3, 4)
    assert g3["prompt"] == pytest.approx(0.03) and g3["skill"] == pytest.approx(-0.02)
    assert h.recent_yield(5, 4)["prompt"] == pytest.approx(-0.01)
    assert h.recent_yield(6, 4)["prompt"] == -math.inf
    prune = h.prune_set(5, 4)
    assert [p["component"] for p in prune] == ["memory", "prompt", "skill"]
    assert prune[1]["accepted_edits_in_incumbent"][0]["edit_id"] == "h1"
    assert h.accepted_counts()["prompt"] == 1 and h.accepted_counts(before_t=0)["prompt"] == 0
    assert h.has(1, "B") and not h.has(1, "C")
    h.replace_round(1)
    assert not h.has(1, "B") and h.has(0, "A")


def test_history_render_keeps_only_last_four_unmeasured(tmp_path):
    h = History(tmp_path / "h.jsonl")
    h.append_candidate(0, "A", [{"id": "m", "component": "prompt"}], "REJECTED", -0.1, 0.0, False, 0.4, 1.0, None,
                       early_stopped=True, delta_C_partial=True)
    for i in range(6):
        h.append_candidate(1 + i, "A", [{"id": f"u{i}", "component": "prompt"}], "no_proposal",
                           None, None, False, None, None, None)
    rows = h.render()
    assert [r["edit_id"] for r in rows] == ["m", "u2", "u3", "u4", "u5"]
    assert rows[0]["early_stopped"] is True and rows[0]["delta_C_partial"] is True
    assert "early_stopped" not in rows[1]


# ---------------------------------------------------------------- frontier --


def test_frontier_seed_settle_and_atomic_save(tmp_path):
    ev = EvalResult(job="base", k=1, per_task={}, S=0.5, C=10.0, n_expected=0, missing=0)
    inc = F.incumbent_entry(0, "c0", "tree0", "sha256:x", "base", ev)
    fr = F.seed("run", inc, {"T": 2})
    win = F.incumbent_entry(1, "c1", "tree1", "sha256:y", "r0A", EvalResult("r0A", 1, {}, 0.7, 11.0, 0, 0), "A")
    fr1 = F.settle(fr, 0, win)
    assert fr1["S_star"] == 0.7 and F.scores(fr1) == [0.5, 0.7] and fr1["incumbent"]["variant"] == "A"
    fr2 = F.settle(fr1, 1, None)
    assert F.scores(fr2) == [0.5, 0.7, 0.7] and fr2["incumbent"]["t"] == 1
    path = tmp_path / "frontier.json"
    F.save(path, fr2)
    assert F.load(path) == json.loads(json.dumps(fr2)) and F.settled_rounds(path) == 2
    assert not (tmp_path / "frontier.json.tmp").exists()


# ----------------------------------------------------------------- tagging --


def _h0_copy(tmp_path, name="cand"):
    d = tmp_path / name
    shutil.copytree(BUILTIN_H0, d)
    return d


def _edit_manifest(d, fn):
    data = json.loads((d / "manifest.json").read_text())
    fn(data)
    (d / "manifest.json").write_text(json.dumps(data, indent=2))


def test_tagging_param_leaves_and_mislabeled_edit(tmp_path):
    inc = load_manifest(BUILTIN_H0)
    d = _h0_copy(tmp_path)
    _edit_manifest(d, lambda m: m["components"][0]["params"].update(
        extra_blocks=[{"id": "x", "text": GOOD}], part_overrides={"efficiency": None}))
    ts = touched(inc, load_manifest(d))
    assert ts.addresses == ["prompt.system.params.extra_blocks", "prompt.system.params.part_overrides.efficiency"]
    assert ts.kinds == ["prompt"] and not ts.violations and not ts.orphans
    tagged = normalize_edits([{"id": "C1", "component": "memory"}, {"id": "C2", "component": "prompt"}], ts)
    assert [e["component"] for e in tagged] == ["prompt", "prompt"]
    assert tagged[0]["declared_component"] == "memory"


def test_tagging_new_component_counts_with_its_declared_kind(tmp_path):
    inc = load_manifest(BUILTIN_H0)
    d = _h0_copy(tmp_path)
    (d / "skills" / "readback").mkdir(parents=True)
    (d / "skills" / "readback" / "SKILL.md").write_text("---\nname: readback\n---\nRead it back once.")

    def add(m):
        m["components"].append({"id": "skills.library", "kind": "skill",
                                "impl": "xgen_rsi.components.skills:SkillLibraryComponent",
                                "files": ["skills/readback/SKILL.md"]})
        m["components"][2]["params"]["repeat_stop_after"] = 2  # control.standard
    _edit_manifest(d, add)
    ts = touched(inc, load_manifest(d))
    assert ts.kinds == ["control_flow", "skill"]
    ops = {t.address: t.op for t in ts.touches}
    assert ops["skills.library"] == "add_component" and ops["skills.library.files.skills/readback/SKILL.md"] == "add_file"
    # per-edit addresses scope the evidence exactly
    tagged = normalize_edits([
        {"id": "C1", "component": "skill", "addresses": ["skills.library"]},
        {"id": "C2", "component": "skill", "addresses": ["control.standard.params.repeat_stop_after"]},
        {"id": "C3", "component": "memory"},  # candidate-wide evidence, first touched kind in K order
    ], ts)
    assert [e["component"] for e in tagged] == ["skill", "control_flow", "control_flow"]


def test_tagging_violations(tmp_path):
    inc = load_manifest(BUILTIN_H0)
    d = _h0_copy(tmp_path)

    def bad(m):
        m["locked"] = []
        m["components"][2]["params"]["max_iterations"] = 50
    _edit_manifest(d, bad)
    (d / "stray.md").write_text("not referenced")
    ts = touched(inc, load_manifest(d))
    text = " ".join(ts.violations)
    assert "'locked' is owned by the platform" in text and "max_iterations" in text
    assert ts.orphans == ("stray.md",)


# ------------------------------------------------------------------ critic --


def test_critic_precheck_patterns(tmp_path):
    dom = make_domain(tmp_path, extra_denylist=["Acme Holdings"])
    pats = dom.critic_patterns()
    whys = {w for _, w in pats}
    assert {"evaluation task id", "verifier check value", "denylisted name"} <= whys

    def diff(added: str) -> str:
        return f"--- a/harness/manifest.json\n+++ b/harness/manifest.json\n@@ -1 +1 @@\n-old\n+{added}\n"

    assert precheck(diff(GOOD), pats) == []
    assert "evaluation task id" in precheck(diff(f"when the request is {EVOLVE_IDS[1]}"), pats)[0]
    assert "verifier check value" in precheck(diff("write STATUS: DONE at the end"), pats)[0]
    assert precheck(diff("as Acme holdings requires"), pats)[0].startswith("denylisted name")
    assert precheck(diff("set XGEN_RSI_HARNESS_DIR"), pats) == ["kernel setting name (XGEN_RSI_*) in diff"]
    assert precheck(diff("workflow rsi-eval"), pats)
    assert precheck(diff('api_key = "abcdefghijkl"'), pats) == ["credential in diff"]
    assert precheck(diff("read outcome.json first"), pats)
    # removed / context lines are incumbent content and are not re-scanned
    assert precheck("-STATUS: DONE\n context STATUS: DONE\n", pats) == []
    # a new file named after a task is caught through its path
    assert precheck(f"+++ b/harness/skills/{EVOLVE_IDS[0]}/SKILL.md\n+body\n", pats)
    assert "harness/skills/x/SKILL.md" in added_text("+++ b/harness/skills/x/SKILL.md\n+a\n")


def test_critic_review_parses_and_rejects_unparseable():
    diff = "+++ b/harness/manifest.json\n+safe text\n"
    ok = review(ScriptedRoleLLM(['noise {"verdict": "accept", "reasons": []} trailing']), diff, "s", "m")
    assert ok["verdict"] == "accept" and ok["risk_notes"] == []
    llm = ScriptedRoleLLM(["not json", '{"verdict": "maybe"}', "still not"])
    out = review(llm, diff, "s", "m")
    assert out["verdict"] == "reject" and "unparseable after 3 attempts" in out["reasons"][0]
    assert len(llm.calls) == 3
    assert "7. PERMISSION / REFUSAL BYPASS" in llm.calls[0]["system"]
    assert "10. USER-FACING COPY" in llm.calls[0]["system"]
    assert review(ScriptedRoleLLM([]), "", "s", "m")["reasons"] == ["empty diff"]


# ------------------------------------------------------------------ render --


def _view(tmp_path) -> TrialView:
    ws = tmp_path / "t__0" / "workspace"
    ws.mkdir(parents=True)
    transcript = [
        {"role": "user", "content": "Do the task."},
        {"role": "assistant", "content": [{"type": "text", "text": "line one\nline two"},
                                          {"type": "tool_use", "id": "w1", "name": "Write",
                                           "input": {"file_path": f"{ws}/report.txt", "content": "x" * 900}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "w1", "content": "ok", "is_error": True}]},
        {"role": "assistant", "content": [{"type": "text", "text": "finished"}]},
    ]
    outcome = {"task_id": "t", "trial": 0, "reward": 0.5, "tokens": 30, "missing": False, "status": "completed",
               "answer": "finished", "verifier": {"checks": [{"name": "exists", "kind": "file_exists", "passed": True},
                                                            {"name": "content", "kind": "file_contains", "passed": False,
                                                             "detail": "text missing"}],
                                                 "valid_output": None, "no_submission": False, "fail_class": "wrong_output"}}
    rec = {"status": "completed", "termination_reason": "model_completed", "transcript": transcript,
           "steps": {"model_calls": 2, "tool_calls": 1, "tool_errors": 1}}
    return TrialView("t", 0, outcome, rec, str(ws.resolve()))


def test_render_trial_layout(tmp_path):
    view = _view(tmp_path)
    text = render_trial(view)
    assert "=== TASK PROMPT ===\nDo the task." in text
    assert "[step 1] AGENT: line one\n[step 1]   line two" in text
    assert "[step 1] TOOL_CALL Write(" in text and "<workspace>/report.txt" in text and str(tmp_path) not in text
    assert "...[+" in text  # args capped
    assert "[step 1] TOOL_ERROR Write: ok" in text
    assert text.rstrip().endswith("valid_output: None | no_submission: False | fail_class: wrong_output")
    assert "check 1 [FAIL] content: text missing" in text
    cut = cut_steps(text, 2, 2, 10_000)
    assert "[step 1]" not in cut and "[step 2] AGENT: finished" in cut and "=== VERIFIER" in cut
    assert "capped at 50" in cut_steps(text, None, None, 50)
    tr = TaskResult(rewards=[0.5, 1.0], weights=[2, 2])
    assert task_row("t", view, tr) == ("t | trial 0 | checks 1/2 (mean over trials 0.750) | status=completed | "
                                       "steps=2 | tokens=30 | failed_checks=[1]")


# --------------------------------------------------------- digester/analyst --


def test_digester_is_jailed_and_capped(tmp_path):
    traces = tmp_path / "rendered"
    traces.mkdir()
    (traces / "t1.txt").write_text("a\nVERIFIER\ncheck 0 [FAIL] x\n")
    (tmp_path / "secret.txt").write_text("outside")
    llm = ScriptedRoleLLM([
        j({"action": "read_file", "path": "../secret.txt"}),
        j({"action": "glob", "pattern": "../*"}),
        j({"action": "bash", "cmd": "cat t1.txt"}),
        j({"action": "grep", "pattern": "FAIL", "path": "t1.txt"}),
        j({"action": "return", "digest": {"blocker": "x" * 7000}}),
        j({"action": "return", "digest": {"blocker": "short"}}),
    ])
    d = digest_task(llm, traces, "t1", "failure", "brief")
    assert d == {"blocker": "short", "task_id": "t1", "lens": "failure"}
    log = llm.calls[-1]["prompt"]
    assert "path escapes the traces dir" in log and "(no matches)" in log
    assert "unknown action bash" in log and "3: check 0 [FAIL] x" in log and "cap is 6000" in log


def test_analyst_report_and_unknown_ids(tmp_path):
    view = _view(tmp_path)
    traces = {"t": view}
    analyst = ScriptedRoleLLM([
        j({"action": "digest_many", "requests": [{"task_id": "t", "lens": "failure"}, {"task_id": "zz"}]}),
        j({"action": "report", "failure_modes": [{"mode": "a", "n_tasks": 1}, {"mode": "b", "n_tasks": 5}],
           "capability_gaps": "not-a-list", "success_habits": []}),
    ])
    digester = ScriptedRoleLLM([j({"action": "return", "digest": {"blocker": "b"}})])
    rep = analyze(analyst, digester, traces, {"t": TaskResult(rewards=[0.5])}, tmp_path / "r0",
                  briefs={"analyst": "A", "digester": "D"}, render=render_trial, task_row=task_row, parallel=1)
    assert [m["mode"] for m in rep["failure_modes"]] == ["b", "a"] and rep["capability_gaps"] == []
    assert rep["n_digests"] == 1
    assert (tmp_path / "r0" / "analysis" / "rendered" / "t.txt").exists()
    assert json.loads((tmp_path / "r0" / "analysis" / "digests" / "t_failure.json").read_text())["task_id"] == "t"
    assert "1 requests skipped: unknown task_id" in analyst.calls[1]["prompt"]


def test_analyst_is_told_the_budget_and_reports_at_the_cap(tmp_path, monkeypatch):
    """상한까지 요약만 요청하는 분석기 — 남은 턴을 알려 주고, 상한에서는 요약 대신 보고를 받는다(빈 분석으로 끝나지 않게)."""
    from xgen_rsi.evolve import analyst as analyst_mod

    monkeypatch.setattr(analyst_mod, "MAX_TURNS", 4)
    view = _view(tmp_path)
    digest = j({"action": "digest_many", "requests": [{"task_id": "t", "lens": "failure"}]})
    analyst = ScriptedRoleLLM([digest, digest, digest, digest,
                               j({"action": "report", "failure_modes": [{"mode": "late", "n_tasks": 1}]})])
    digester = ScriptedRoleLLM([j({"action": "return", "digest": {"blocker": "b"}})] * 8)
    rep = analyze(analyst, digester, {"t": view}, {"t": TaskResult(rewards=[0.2])}, tmp_path / "r0",
                  briefs={"analyst": "A", "digester": "D"}, render=render_trial, task_row=task_row, parallel=1)
    assert [m["mode"] for m in rep["failure_modes"]] == ["late"] and "error" not in rep
    prompts = [c["prompt"] for c in analyst.calls]
    assert "BUDGET: 3 turn(s) left" in prompts[1] and "BUDGET EXHAUSTED" in prompts[4]
    assert rep["n_digests"] == 4
    assert "report (1 failure modes)" in (tmp_path / "r0" / "analysis" / "transcript.txt").read_text()


# ------------------------------------------------------------------ gitops --


def test_harness_repo_worktrees_and_local_author(tmp_path):
    repo = HarnessRepo(tmp_path / "repo")
    repo.init(BUILTIN_H0, "evolve/x")
    repo.init(BUILTIN_H0, "evolve/x")  # idempotent
    assert repo.branch_exists("evolve/x") and (tmp_path / "repo" / "harness" / "manifest.json").exists()
    assert git(repo.root, "config", "--local", "user.email").stdout.strip() == AUTHOR_EMAIL
    base = repo.rev("evolve/x")
    wt = repo.worktree_new_branch(tmp_path / "wt" / "r0A", "x/r0A", "evolve/x")
    (repo.harness_dir(wt) / "skills").mkdir()
    (repo.harness_dir(wt) / "skills" / "new.md").write_text("new file\n")
    diff = repo.diff_with_new_files(wt)
    assert "+new file" in diff and "harness/skills/new.md" in diff
    commit = repo.commit(wt, "r0A: add a file")
    log = subprocess.run(["git", "log", "-1", "--format=%an <%ae>", commit], cwd=repo.root,
                         capture_output=True, text=True).stdout.strip()
    assert log == f"{AUTHOR_NAME} <{AUTHOR_EMAIL}>"
    assert repo.tree_hash(commit) != repo.tree_hash(base) and repo.is_ancestor(base, commit)
    repo.fast_forward("evolve/x", commit)
    assert repo.rev("evolve/x") == commit
    with pytest.raises(RuntimeError):
        repo.fast_forward("evolve/x", base)
    a = repo.worktree_detached(tmp_path / "wt" / "a", base)
    dd = diff_dirs(repo.harness_dir(a), repo.harness_dir(wt))
    assert "+new file" in dd
    repo.worktree_remove(a)
    repo.worktree_remove(wt, "x/r0A")
    assert not wt.exists() and not repo.branch_exists("x/r0A")
    assert "harness/skills/new.md" in repo.diff_refs(base, commit)
    assert repo.harness_version(base) == load_manifest(BUILTIN_H0).version_id()
    # an unreferenced file has no effect, so the manifest version does not change (done() refuses it)
    assert repo.harness_version(commit) == repo.harness_version(base)


# ------------------------------------------------------------------ domain --


def test_domain_smoke_ids_guards_and_constitution(tmp_path):
    dom = make_domain(tmp_path, max_valid_rate_drop=0.03)
    per = {i: TaskResult(rewards=[r]) for i, r in zip(EVOLVE_IDS, [0.2, 0.9, 0.5])}
    assert dom.smoke_ids(per, n=2) == [EVOLVE_IDS[1], EVOLVE_IDS[2]]
    assert dom.smoke_ids(None, n=1) == [EVOLVE_IDS[0]]
    inc = EvalResult("a", 1, {}, 0.5, 1.0, 0, 0, extra={"valid_rate": 0.9, "no_submission_rate": 0.0})
    cand = EvalResult("b", 1, {}, 0.6, 1.0, 0, 0, extra={"valid_rate": 0.8, "no_submission_rate": 0.5})
    assert len(dom.guards(inc, cand)) == 1  # only the configured valid-rate guard
    assert len(dom.guards(inc, cand, max_nosub_rise=0.02)) == 2
    assert make_domain(tmp_path / "x").guards(inc, cand) == []
    skill, patterns = dom.constitution()
    assert "Hard rules" in skill and "subagent** kind is disabled" in skill and "Pattern Library" in patterns
    assert dom.regression_threshold(2) == 0.5
    assert any(CHECKS[1]["text"] in p.replace("\\", "") for p, _ in dom.critic_patterns())


# ------------------------------------------------------------------ driver --


class _FakeRun:
    def __init__(self, tmp: Path, fail_rounds: int = 0, T: int = 3) -> None:
        self.cfg = SimpleNamespace(T=T, delta=0.1)
        self.frontier_path = tmp / "frontier.json"
        self.calibration_path = tmp / "calibration.json"
        self.stop_path = tmp / "STOP"
        self.logs = tmp / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.fail_rounds = fail_rounds
        self.calls = []
        self.lines = []

    def log(self, msg):
        self.lines.append(msg)

    def frontier(self):
        return json.loads(self.frontier_path.read_text())

    def baseline(self, job="base"):
        self.calls.append("baseline")
        self.frontier_path.write_text(json.dumps({"incumbent": {"t": 0, "commit": "c", "S": 0.5},
                                                  "trajectory": [{"t": 0, "S": 0.5}]}))

    def calibrate(self, jobs):
        self.calls.append("calibrate")

    def round(self, t):
        self.calls.append(t)
        if self.fail_rounds > 0:
            self.fail_rounds -= 1
            raise RuntimeError("infra down")
        fr = self.frontier()
        fr["trajectory"].append({"t": t + 1, "S": 0.5})
        self.frontier_path.write_text(json.dumps(fr))


def test_driver_retries_and_stops_after_consecutive_failures(tmp_path):
    ok = _FakeRun(tmp_path / "a", fail_rounds=2)
    out = drive(ok)
    assert out["status"] == "done" and out["settled"] == 3 and ok.calls == ["baseline", 0, 0, 0, 1, 2]
    assert len(out["failures"]) == 2 and (tmp_path / "a" / "logs" / "r0.error.log").exists()
    bad = _FakeRun(tmp_path / "b", fail_rounds=10)
    out = drive(bad)
    assert out["status"] == "infra_failures" and bad.calls == ["baseline", 0, 0, 0]
    stop = _FakeRun(tmp_path / "c")
    stop.stop_path.write_text("")
    assert drive(stop)["status"] == "stopped" and stop.calls == ["baseline"]
