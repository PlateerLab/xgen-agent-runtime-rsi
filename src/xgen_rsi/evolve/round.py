"""One RRSI round of one evolution run: Algorithm 1 (proposal side) then Algorithm 2 (selection).

    run.round(t)
      1. F_t ← Analyze(H_t, D_evolve)              analyst over the incumbent's own evaluation
      2. b_t ← edit_budget(t, T, b_min, b_max)      rsi_math (Eq.4)
      3. σ_t, 𝒯_t, 𝒰_t, 𝓔_t, 𝓑_t                  rsi_math via History (Eq.11/13/14)
      4. for each of m variants (own worktree + branch <name>/r{t}{v}):
             C ~ P_reg(· | H_t, F_t, 𝓛_t, b_t, 𝓔_t, 𝓑_t)   propose.propose
             critic + bounded repair, tag normalization   critic.review, tagging (D10)
             reserved exploration slot judged by the normalized tags
             smoke: manifest loads/instantiates + 1–2 tasks with k = 1 (liveness, not selection)
      5. Evaluate(H', D_evolve, k), exact-bound early stop when enabled (33 §4)
      6. Algorithm 2: rsi_math.select_round → decisions.json, history records, attribution
      7. frontier (atomic) then fast-forward evolve/<name> to the winner

Everything under the run directory is resume-safe: analysis_report.json, prep.json per variant,
eval.json per variant and the per-trial outcomes are reused; history records are written once per
(t, variant); the frontier is the source of truth and the branch ref is reconciled to it.

Early-stopped candidates (33 §4): decision REJECTED with reason_code "floor", recorded
ΔS = ``early_stop_record_delta`` (an upper bound), ``early_stopped=True``, ΔC from the partial
tokens flagged ``delta_C_partial``. ``readjudicate`` re-checks the stop condition with the new
δ / S★ and refuses (NeedsReevaluation) instead of guessing when it no longer holds.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/loop.py``: the round structure),
Copyright 2026 The rrsi Authors / Google LLC, Apache License 2.0; modified by PlateerLab.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from xgen_rsi.evolve import frontier as F
from xgen_rsi.evolve.analyst import analyze, load_digests
from xgen_rsi.evolve.config import ROLES, EvolveConfig
from xgen_rsi.evolve.critic import review
from xgen_rsi.evolve.domain import EvolveDomain
from xgen_rsi.evolve.gitops import HarnessRepo
from xgen_rsi.evolve.history import History, dumps_line, read_jsonl
from xgen_rsi.evolve.propose import propose
from xgen_rsi.evolve.runner import EarlyStop, evaluate, load_outcomes
from xgen_rsi.evolve.tagging import TouchSet, normalize_edits, touched
from xgen_rsi.evolve.tasks import TaskSpec
from xgen_rsi.harness.runtime import instantiate
from xgen_rsi.harness.spec import HarnessManifest, load_manifest
from xgen_rsi.kernel.executor import BUILTIN_H0
from xgen_rsi.rsi_math import (
    K_STR,
    Candidate,
    Decision,
    EvalResult,
    Exploration,
    attribute,
    calibrate,
    can_stop_exactly,
    early_stop_record_delta,
    edit_budget,
    exploration,
    outcome_of,
    reserved_variants,
    select_round,
    stall_flag,
    upper_lower,
    valid_measurement,
)

VARIANT_LABELS = "ABCDEFGH"


class EvolveError(RuntimeError):
    """A run cannot proceed (missing baseline, inconsistent state, invalid baseline …)."""


class NeedsReevaluation(EvolveError):
    """Re-adjudication found early-stopped candidates whose stop condition no longer holds."""

    def __init__(self, t: int, variants: Sequence[str]) -> None:
        self.t = t
        self.variants = list(variants)
        super().__init__(f"round {t}: early-stopped candidates {self.variants} must be re-evaluated "
                         f"(the stop condition no longer holds); run reevaluate({t}, {self.variants})")


@dataclass
class Roles:
    """The four search roles; any object with ``generate(prompt, system=, json_only=, cache_prefix=)``."""

    proposer: Any
    critic: Any
    analyst: Any
    digester: Any

    @classmethod
    def from_config(cls, cfg: EvolveConfig) -> "Roles":
        from xgen_rsi.roles.llm import RoleLLM

        built: Dict[str, Any] = {}
        for role in ROLES:
            model = cfg.role_model(role)
            if model is None:
                raise EvolveError(f"no model configured for role {role!r} (set roles.{role} in rrsi.json)")
            built[role] = RoleLLM(model, role=role)
        return cls(**built)


@dataclass(frozen=True)
class EarlyInfo:
    """Partial evaluation state of an early-stopped candidate (33 §4)."""

    A: float
    B: float
    R: float
    S_partial: float
    S_upper: float
    delta_S: float

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Draft:
    """A candidate while the round runs (mutable; converted to ``rsi_math.Candidate`` to judge)."""

    variant: str
    edits: List[Dict[str, Any]] = field(default_factory=list)
    diff_path: Optional[str] = None
    branch: Optional[str] = None
    commit: Optional[str] = None
    harness_version: Optional[str] = None
    gate_failure: Optional[str] = None
    detail: str = ""
    ev: Optional[EvalResult] = None
    early: Optional[EarlyInfo] = None

    @property
    def job_suffix(self) -> str:
        return self.variant


def exploration_directive(expl: Exploration) -> Dict[str, Any]:
    """𝓔_t plus the sentence handed to the proposer (reference ``history.exploration``)."""
    untried = list(expl.untried)
    if expl.sigma and untried:
        text = (f"STALL: the incumbent has not moved by more than the noise band over the last "
                f"rounds (sigma_t = 1). {expl.m_draft} candidate slot(s) this round are RESERVED "
                f"for exploratory edits on component kinds the run has never exercised: "
                f"{untried}. A variant holding a reserved slot must put at least one edit on one "
                f"of those kinds.")
    elif untried:
        text = (f"Component kinds not yet exercised in this run: {untried}. Not mandatory this "
                f"round (sigma_t = 0), but evidence about them is still missing.")
    else:
        text = "Every enabled component kind has been exercised at least once."
    return {"sigma": expl.sigma, "untried": untried, "m_draft": expl.m_draft, "text": text}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=True, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_eval(path: Path) -> EvalResult:
    return EvalResult.from_json(json.loads(path.read_text(encoding="utf-8")))


def save_eval(path: Path, ev: EvalResult) -> None:
    _write_json(path, ev.to_json())


class EvolveRun:
    """Paths, state and git plumbing of one evolution run (reference ``loop.Run``)."""

    def __init__(self, domain: EvolveDomain, cfg: EvolveConfig, run_dir: Path | str, *,
                 roles: Optional[Roles] = None, start_harness: Path | str = BUILTIN_H0,
                 name: Optional[str] = None, log: Optional[Callable[[str], None]] = None) -> None:
        self.domain, self.cfg = domain, cfg
        self.name = name or domain.name
        self.run_dir = Path(run_dir).resolve()  # git worktree 경로가 하네스 저장소 기준으로 풀리지 않게 절대 경로로
        self.jobs = self.run_dir / "jobs"
        self.wt_root = self.run_dir / "wt"
        self.logs = self.run_dir / "logs"
        self.frontier_path = self.run_dir / "frontier.json"
        self.calibration_path = self.run_dir / "calibration.json"
        self.global_analysis = self.run_dir / "global_analysis.json"
        self.attribution_path = self.run_dir / "attribution.jsonl"
        self.stop_path = self.run_dir / "STOP"
        self.history = History(self.run_dir / "history.jsonl")
        self.branch = f"evolve/{self.name}"
        if cfg.m > len(VARIANT_LABELS):
            raise EvolveError(f"m = {cfg.m} exceeds the {len(VARIANT_LABELS)} variant labels")
        self._roles = roles
        self._log_fn = log
        self._log_lock = threading.Lock()
        for d in (self.run_dir, self.jobs, self.wt_root, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        self.repo = HarnessRepo(self.run_dir / "harness_repo")
        self.repo.init(start_harness, self.branch)

    # ------------------------------------------------------------- plumbing --
    @property
    def roles(self) -> Roles:
        if self._roles is None:
            self._roles = Roles.from_config(self.cfg)
        return self._roles

    def log(self, msg: str) -> None:
        line = f"[rsi:{self.name}] {time.strftime('%H:%M:%S')} {msg}"
        with self._log_lock:
            if self._log_fn is not None:
                self._log_fn(line)
            else:
                print(line, flush=True)
            with open(self.logs / "evolve.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def frontier(self) -> Dict[str, Any]:
        try:
            return F.load(self.frontier_path)
        except F.FrontierError as exc:
            raise EvolveError(str(exc)) from exc

    def delta(self) -> float:
        if self.cfg.delta is not None:
            return float(self.cfg.delta)
        cal = _read_json(self.calibration_path)
        if cal is not None:
            return float(cal["delta"])
        raise EvolveError("no noise band: set delta in the config or run calibrate")

    def eval_path(self, job: str) -> Path:
        return self.jobs / job / "eval.json"

    def enabled_kinds(self, manifest: HarnessManifest) -> List[str]:
        """𝒦_enabled: the mode's active kinds that the harness itself enables (decision D-3)."""
        return [k for k in self.cfg.mode_config.enabled if k in manifest.enabled_kinds]

    def guard_fn(self, inc: EvalResult, cand: EvalResult) -> List[str]:
        return self.domain.guards(inc, cand, max_valid_drop=self.cfg.max_valid_rate_drop,
                                  max_nosub_rise=self.cfg.max_nosub_rise)

    def reconcile(self) -> None:
        """Bring ``evolve/<name>`` to the frontier's incumbent commit (the frontier is written
        first, so a crash between the two leaves only the ref behind)."""
        if not self.frontier_path.exists():
            return
        fr = self.frontier()
        commit = str(fr["incumbent"]["commit"])
        tip = self.repo.rev(self.branch)
        if tip == commit:
            return
        if self.repo.tree_hash(commit) != fr["incumbent"]["harness_tree"]:
            raise EvolveError(f"frontier incumbent {commit} does not match its recorded harness tree")
        self.log(f"reconciling {self.branch}: {tip[:12]} -> frontier incumbent {commit[:12]}")
        self.repo.update_ref(self.branch, commit)

    def _run_eval(self, hdir: Path, tasks: Sequence[TaskSpec], k: int, job: str,
                  early_stop: Optional[EarlyStop]) -> EvalResult:
        return evaluate(str(hdir), list(tasks), k, policy=self.domain.policy,
                        out_dir=str(self.jobs / job), job=job, parallel=self.cfg.trial_parallel,
                        client_factory=self.domain.client_factory, early_stop=early_stop,
                        seed=self.cfg.seed)

    # ------------------------------------------------------------- baseline --
    def baseline(self, job: str = "base") -> EvalResult:
        """Evaluate H_0 (the tip of evolve/<name>) and seed the frontier."""
        if self.frontier_path.exists() and len(self.frontier()["trajectory"]) > 1:
            raise EvolveError("rounds already exist; a new baseline would discard them")
        wt = self.repo.worktree_detached(self.wt_root / "base", self.branch)
        try:
            hdir = self.repo.harness_dir(wt)
            manifest = load_manifest(hdir)
            tasks = self.domain.evolve_tasks()
            self.log(f"baseline: {len(tasks)} tasks x k={self.cfg.k} -> job {job}")
            ev = self._run_eval(hdir, tasks, self.cfg.k, job, None)
            save_eval(self.eval_path(job), ev)
            if not valid_measurement(ev, self.cfg.invalid_missing_frac):
                raise EvolveError(f"baseline invalid: {ev.missing}/{ev.n_expected} trials missing")
            commit = self.repo.rev(self.branch)
            inc = F.incumbent_entry(0, commit, self.repo.tree_hash(commit), manifest.version_id(), job, ev)
            F.save(self.frontier_path, F.seed(self.name, inc, self.cfg.dump()))
            self.history.set_baseline({"t": 0, "variant": "-", "edit_id": None, "component": None,
                                       "hypothesis": "H_0 baseline", "outcome": "BASELINE",
                                       "S": ev.S, "C": ev.C, "accepted": True, "delta_S": None,
                                       "delta_C": None, "bundle": 0})
            self.log(f"baseline S={ev.S:.4f} C={ev.C} missing={ev.missing}/{ev.n_expected}")
            return ev
        finally:
            self.repo.worktree_remove(wt)

    def _h0_commit(self) -> str:
        if self.frontier_path.exists():
            return str(self.frontier()["trajectory"][0]["commit"])
        return self.repo.rev(self.branch)

    def calibrate(self, jobs: Sequence[str]) -> Dict[str, Any]:
        """δ from R ≥ 1 independent evaluations of H_0 (``rsi_math.calibrate``, 05 §2.4). Jobs
        without an eval.json are evaluated first (R ≥ 2 recommended: the direct estimate)."""
        if not jobs:
            raise EvolveError("calibrate needs at least one job")
        evals: List[EvalResult] = []
        for job in jobs:
            path = self.eval_path(job)
            if path.exists():
                evals.append(load_eval(path))
                continue
            wt = self.repo.worktree_detached(self.wt_root / f"cal_{job}", self._h0_commit())
            try:
                ev = self._run_eval(self.repo.harness_dir(wt), self.domain.evolve_tasks(), self.cfg.k, job, None)
            finally:
                self.repo.worktree_remove(wt)
            save_eval(path, ev)
            if not valid_measurement(ev, self.cfg.invalid_missing_frac):
                raise EvolveError(f"calibration job {job} invalid: {ev.missing}/{ev.n_expected} missing")
            evals.append(ev)
        if len(evals) < 2:
            self.log("calibrate: R = 1 uses the within-task bootstrap; R >= 2 repeated base "
                     "evaluations are recommended")
        cal = calibrate(evals, z=self.cfg.params.delta_z).to_json()
        cal["jobs"] = list(jobs)
        _write_json(self.calibration_path, cal)
        self.log(f"calibrated delta={cal['delta']:.5f} (sd_null {cal['sd_null']:.5f}, z={cal['z']}, {cal['method']})")
        return cal

    def heldout(self, label: str, split: str, ref: Optional[str] = None,
                k: Optional[int] = None) -> EvalResult:
        """Evaluate ``ref`` (default: the incumbent) on a held-out split. Report only — never
        used by any decision."""
        tasks = self.domain.split_tasks(split)
        target = ref or self.branch
        wt = self.repo.worktree_detached(self.wt_root / f"heldout_{label}", target)
        job = f"heldout_{label}"
        try:
            ev = self._run_eval(self.repo.harness_dir(wt), tasks, k or self.cfg.k, job, None)
        finally:
            self.repo.worktree_remove(wt)
        save_eval(self.eval_path(job), ev)
        self.log(f"heldout {label} ({split}) @ {self.repo.rev(target)[:12]}: S={ev.S:.4f} C={ev.C} "
                 f"missing={ev.missing}/{ev.n_expected}")
        return ev

    def status(self) -> Dict[str, Any]:
        if not self.frontier_path.exists():
            return {"name": self.name, "settled_rounds": -1}
        fr = self.frontier()
        return {"name": self.name, "settled_rounds": len(fr["trajectory"]) - 1,
                "incumbent": {k: fr["incumbent"].get(k) for k in ("t", "commit", "job", "S", "C", "variant")},
                "S_star": fr["S_star"], "trajectory": [x["S"] for x in fr["trajectory"]]}

    # ------------------------------------------------------------- evidence --
    def build_traces(self, job: str, per_task: Mapping[str, Any]) -> Dict[str, Any]:
        """Worst trial of the lowest-scoring tasks plus the best trial of the highest-scoring
        ones (success habits the proposer must not break)."""
        ranked = sorted(per_task.items(), key=lambda kv: kv[1].mean)
        fails = [t for t, _ in ranked[: self.cfg.n_fail_traces]]
        tail = ranked[-self.cfg.n_success_traces:] if self.cfg.n_success_traces > 0 else []
        wins = [t for t, _ in tail if t not in fails]
        traces: Dict[str, Any] = {}
        for tid in fails + wins:
            tr = per_task[tid]
            if not tr.rewards:
                continue
            rewards = list(tr.rewards)
            idx = rewards.index(min(rewards)) if tid in fails else rewards.index(max(rewards))
            view = self.domain.load_trial(self.jobs / job, tid, idx)
            if view is not None:
                traces[tid] = view
        return traces

    def scoreboard(self) -> List[Dict[str, Any]]:
        return read_jsonl(self.attribution_path)[-20:]

    def _attribute(self, t: int, d: Draft, inc_ev: EvalResult) -> None:
        if d.ev is None:
            return
        thr = self.domain.regression_threshold(self.cfg.k)
        rows = []
        for e in d.edits:
            row = attribute(e, inc_ev, d.ev, self.cfg.k, t=t, variant=d.variant, threshold=thr).to_json()
            if d.early is not None:
                row["partial"] = True
            rows.append(row)
        with open(self.attribution_path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(dumps_line(r))

    # ---------------------------------------------------------------- round --
    def round(self, t: int, dry_run: bool = False) -> Optional[Dict[str, Any]]:
        d, cfg = self.domain, self.cfg
        self.reconcile()
        fr = self.frontier()
        inc = fr["incumbent"]
        tree = self.repo.tree_hash(self.branch)
        if inc["harness_tree"] != tree:
            raise EvolveError(f"harness tree of {self.branch} != frontier ({tree} vs {inc['harness_tree']})")
        if len(fr["trajectory"]) < t + 1:
            raise EvolveError(f"round {t} needs trajectory up to t={t}; have {len(fr['trajectory'])} "
                              f"entries (run earlier rounds)")
        if len(fr["trajectory"]) > t + 1:
            raise EvolveError(f"round {t} is already settled")
        delta = self.delta()
        S_star = float(fr["S_star"])
        rdir = self.run_dir / f"r{t}"
        rdir.mkdir(exist_ok=True)
        tasks = d.evolve_tasks()
        inc_ev = load_eval(self.eval_path(inc["job"]))
        inc_wt = self.repo.worktree_detached(self.wt_root / f"inc_r{t}", self.branch)
        drafts: List[Draft] = []
        try:
            inc_manifest = load_manifest(self.repo.harness_dir(inc_wt))
            enabled = self.enabled_kinds(inc_manifest)
            self.log(f"=== round {t}/{cfg.T} incumbent t={inc['t']} {str(inc['commit'])[:12]} "
                     f"S={inc_ev.S:.4f} C={inc_ev.C} S*={S_star:.4f} delta={delta:.5f} ===")

            # 1) F_t <- Analyze(H_t, D_evolve)
            traces = self.build_traces(inc["job"], inc_ev.per_task)
            need = 0.5 * min(len(tasks), cfg.n_fail_traces + cfg.n_success_traces)
            if len(traces) < need:
                raise EvolveError(f"only {len(traces)} traces available from {inc['job']}")
            report_path = rdir / "analysis_report.json"
            report = _read_json(report_path)
            if report is not None:
                self.log("reusing analysis_report.json")
            else:
                prior = _read_json(self.global_analysis, {}) or {}
                report = analyze(self.roles.analyst, self.roles.digester, traces, inc_ev.per_task, rdir,
                                 briefs=d.briefs, render=d.render_trace, task_row=d.task_row,
                                 prior_modes=prior.get("failure_modes"),
                                 prior_habits=prior.get("success_habits"),
                                 parallel=cfg.digest_parallel)
                _write_json(report_path, report)
            _write_json(self.global_analysis, {"failure_modes": report.get("failure_modes"),
                                               "success_habits": report.get("success_habits")})
            self.log(f"top modes: {[m.get('mode') for m in (report.get('failure_modes') or [])[:5]]}")
            if dry_run:
                self.log("dry-run: stopping before propose")
                return None

            # 2-3) b_t, sigma_t, T_t, U_t, E_t, B_t
            budget = edit_budget(t, cfg.T, cfg.params.b_min, cfg.params.b_max)
            sigma = stall_flag(F.scores(fr), t, cfg.params.w, delta)
            tried_set = self.history.tried(before_t=t)
            expl = exploration(sigma, tried_set, cfg.params.m_draft, enabled=enabled)
            explore = exploration_directive(expl)
            reserved = reserved_variants(cfg.m, cfg.params.m_draft, sigma, expl.untried)
            prune = self.history.prune_set(t, cfg.params.n_prune)
            ctx = {
                "t": t, "rdir": rdir, "report": report, "digests": load_digests(rdir), "traces": traces,
                "inc_ev": inc_ev, "inc_manifest": inc_manifest, "budget": budget, "explore": explore,
                "prune": prune, "hist_rows": self.history.render(), "enabled": enabled,
                "constitution": d.constitution(), "selection": self._selection_text(S_star, delta, inc_ev, enabled),
            }
            self.log(f"b_t={budget} sigma_t={sigma} untried={list(expl.untried)} "
                     f"reserved={[VARIANT_LABELS[v] for v in sorted(reserved)]} "
                     f"prune={[p['component'] for p in prune]}")
            _write_json(rdir / "directives.json", {
                "t": t, "b_t": budget, "sigma_t": sigma, "tried": sorted(tried_set), "explore": explore,
                "reserved_variants": [VARIANT_LABELS[v] for v in sorted(reserved)], "prune_set": prune,
                "delta": delta, "S_star": S_star, "S_inc": inc_ev.S, "enabled_kinds": enabled,
                "m": cfg.m, "mode": cfg.mode, "early_stop": cfg.use_early_stop})

            # 4) draw and screen m candidates
            for v in range(cfg.m):
                drafts.append(self._draft(VARIANT_LABELS[v], v in reserved, ctx))

            # 5) Evaluate(H', D_evolve, k) for the screened set
            live = [x for x in drafts if x.gate_failure is None]
            with ThreadPoolExecutor(max_workers=max(1, cfg.eval_parallel)) as ex:
                list(ex.map(lambda x: self._evaluate(t, x, rdir, tasks, inc_ev, S_star, delta), live))

            # 6) Algorithm 2
            counts = self.history.accepted_counts(before_t=t)
            winner, decisions = self._judge(drafts, inc_ev, S_star, delta, counts, enabled)
            self._write_decisions(rdir, t, drafts, decisions, winner, inc_ev, S_star, delta, counts,
                                  enabled, readjudicated=False)
            for x, dec in zip(drafts, decisions):
                if self.history.has(t, x.variant):
                    continue  # resumed round: already recorded
                self._record(t, x, dec, winner)
                if x.ev is not None:
                    self._attribute(t, x, inc_ev)

            # 7) H_{t+1}: frontier first (atomic), then the branch ref
            new_inc = None
            if winner is not None and winner.ev is not None and winner.commit:
                new_inc = F.incumbent_entry(t + 1, winner.commit, self.repo.tree_hash(winner.commit),
                                            winner.harness_version or "", f"r{t}{winner.variant}",
                                            winner.ev, winner.variant)
            fr2 = F.settle(fr, t, new_inc)
            F.save(self.frontier_path, fr2)
            if new_inc is not None:
                self.repo.fast_forward(self.branch, str(new_inc["commit"]))
                self.log(f"ACCEPTED r{t}{winner.variant if winner else ''} -> {str(new_inc['commit'])[:12]} "
                         f"S={new_inc['S']:.4f} S*={fr2['S_star']:.4f}")
            else:
                self.log(f"no admissible candidate; H_{t + 1} = H_{t}")
            return {"winner": winner.variant if winner else None,
                    "decisions": [dec.to_json() for dec in decisions]}
        finally:
            for x in drafts:
                self.repo.worktree_remove(self.wt_root / f"r{t}{x.variant}")
            self.repo.worktree_remove(inc_wt)

    def _selection_text(self, S_star: float, delta: float, inc_ev: EvalResult,
                        enabled: Sequence[str]) -> str:
        p = self.cfg.params
        tie = ("ties on S prefer lower cost, then fewer edits, then the variant label"
               if self.cfg.mode_config.select_tie == "xgen" else "ties on S go to the first variant")
        structural = [k for k in K_STR if k in enabled]
        return (f"- Incumbent S_t = {inc_ev.S:.4f}, best ever S* = {S_star:.4f}, noise band delta = {delta:.4f}.\n"
                f"- Noise-adjusted floor: S' must be >= S* - delta.\n"
                f"- Gain larger than delta: relative growth of mean policy tokens per trial must be "
                f"<= {p.beta0} + {p.beta1} x gain.\n"
                f"- Gain within delta: kept only if {p.w_s} x gain - {p.w_c} x relative_cost_change "
                f"+ {p.w_n} x novelty > 0, novelty = structural kinds {structural} the incumbent "
                f"never had an accepted edit on.\n"
                f"- Among admissible candidates the highest S wins ({tie}); otherwise H_t is kept.")

    # ---------------------------------------------------------------- draft --
    def _tag(self, inc_manifest: HarnessManifest, hdir: Path,
             edits: Sequence[Mapping[str, Any]]) -> Tuple[TouchSet, List[Dict[str, Any]]]:
        ts = touched(inc_manifest, load_manifest(hdir))
        return ts, normalize_edits(edits, ts, self.domain.component_signals)

    def _draft(self, vid: str, reserved: bool, ctx: Mapping[str, Any]) -> Draft:
        d, cfg = self.domain, self.cfg
        t, rdir = int(ctx["t"]), Path(ctx["rdir"])
        vdir = rdir / vid
        vdir.mkdir(exist_ok=True)
        branch = f"{self.name}/r{t}{vid}"
        wt = self.wt_root / f"r{t}{vid}"
        prep_path = vdir / "prep.json"
        prep = _read_json(prep_path)
        if prep is not None:  # resume: drafted (and committed or dropped) in an earlier attempt
            if prep.get("gate_failure"):
                return Draft(vid, list(prep.get("edits") or []), diff_path=prep.get("diff_path"),
                             gate_failure=prep["gate_failure"], detail=prep.get("detail", ""))
            if prep.get("commit") and self.repo.branch_exists(branch):
                self.repo.worktree_checkout(wt, branch)
                self.log(f"{vid}: resuming committed candidate {str(prep['commit'])[:12]}")
                return Draft(vid, list(prep.get("edits") or []), diff_path=prep.get("diff_path"),
                             branch=branch, commit=prep["commit"], harness_version=prep.get("harness_version"))
        self.repo.worktree_new_branch(wt, branch, self.branch)
        hdir = self.repo.harness_dir(wt)
        inc_manifest: HarnessManifest = ctx["inc_manifest"]
        explore: Mapping[str, Any] = ctx["explore"]
        untried = list(explore.get("untried") or [])
        skill_md, patterns_md = ctx["constitution"]
        patterns = d.critic_patterns()
        variant_brief = (f"You are variant {vid} of round {t}. {cfg.m} variants are drafted "
                         f"independently from the same incumbent this round and each is evaluated "
                         f"on the full evolve set; the best admissible one becomes H_{t + 1}.")

        def finish(gate: str, detail: str = "", edits: Optional[List[Dict[str, Any]]] = None,
                   diff_path: Optional[str] = None) -> Draft:
            _write_json(prep_path, {"gate_failure": gate, "detail": detail, "edits": edits or [],
                                    "diff_path": diff_path})
            self.repo.worktree_remove(wt, branch)
            self.log(f"{vid}: {gate} {detail[:200]}")
            return Draft(vid, list(edits or []), diff_path=diff_path, gate_failure=gate, detail=detail)

        def run_proposer(repair: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            return propose(self.roles.proposer, hdir, report=ctx["report"], history_rows=ctx["hist_rows"],
                           skill_md=skill_md, patterns_md=patterns_md, budget=int(ctx["budget"]),
                           explore=explore, reserved_slot=reserved, prune_set=ctx["prune"],
                           enabled_kinds=ctx["enabled"], render=d.render_trace, task_row=d.task_row,
                           traces=ctx["traces"], per_task=ctx["inc_ev"].per_task, findings=ctx["digests"],
                           scoreboard=self.scoreboard(), variant_brief=variant_brief,
                           repair_brief=repair, brief=d.briefs.get("proposer", ""),
                           incumbent=inc_manifest, selection=ctx["selection"],
                           prior_changes=repair is not None)

        prop = run_proposer(None)
        _write_json(vdir / "proposal.json", prop)
        if prop["status"] != "done" or prop["n_edits"] == 0:
            return finish("no_proposal", str(prop.get("reason") or prop["status"]))

        # Critic(H_t, H') with bounded repair; tags validated against the touched addresses
        verdict: Dict[str, Any] = {}
        for attempt in range(1 + cfg.repair_rounds):
            diff = self.repo.diff_with_new_files(wt)
            (vdir / "diff.patch").write_text(diff, encoding="utf-8")
            verdict = review(self.roles.critic, diff, str(prop.get("mechanism") or ""),
                             str(prop.get("targets_mode") or ""), edits=prop.get("edits"),
                             patterns=patterns, brief=d.briefs.get("critic", ""))
            if verdict.get("verdict") == "accept":
                try:
                    _, tagged = self._tag(inc_manifest, hdir, prop.get("edits") or [])
                except Exception as exc:  # noqa: BLE001 — a manifest broken after done() is repairable
                    verdict = {"verdict": "reject", "reasons": [f"manifest error: {exc}"],
                               "risk_notes": verdict.get("risk_notes")}
                    tagged = []
                for e in tagged:
                    if e["component"] != e["declared_component"]:
                        self.log(f"{vid}: edit {e.get('id')} declared '{e['declared_component']}' but "
                                 f"the diff touches '{e['component']}'; re-tagged")
                if verdict.get("verdict") == "accept" and reserved and untried and not any(
                        e["component"] in untried for e in tagged):
                    verdict = {"verdict": "reject",
                               "reasons": [f"this variant holds a RESERVED EXPLORATION SLOT: at least "
                                           f"one edit must be on a never-exercised component kind from "
                                           f"{untried}, judged by the touched addresses, and none is"],
                               "risk_notes": verdict.get("risk_notes")}
            _write_json(vdir / f"critic_a{attempt}.json", verdict)
            if verdict.get("verdict") == "accept" or attempt >= cfg.repair_rounds:
                break
            self.log(f"{vid}: critic objections (repair {attempt + 1}/{cfg.repair_rounds}): "
                     f"{str(verdict.get('reasons'))[:200]}")
            prop = run_proposer({"reasons": verdict.get("reasons"), "risk_notes": verdict.get("risk_notes"),
                                 "your_declared_edits": prop.get("edits")})
            _write_json(vdir / f"proposal_r{attempt + 1}.json", prop)
            if prop["status"] != "done":
                break
        _write_json(vdir / "critic.json", verdict)
        edits = list(prop.get("edits") or [])
        diff_path = str(vdir / "diff.patch")
        if verdict.get("verdict") != "accept":
            return finish("critic_reject", str(verdict.get("reasons"))[:600], edits, diff_path)

        # tag (l', h', d'): the declared component checked against the touched addresses
        diff = self.repo.diff_with_new_files(wt)
        (vdir / "diff.patch").write_text(diff, encoding="utf-8")
        ts, edits = self._tag(inc_manifest, hdir, edits)
        _write_json(vdir / "tags.json", ts.to_json())
        commit = self.repo.commit(wt, f"r{t}{vid}: {prop.get('mechanism')}")
        version = load_manifest(hdir).version_id()

        # liveness smoke: does the candidate run at all (not a selection rule)
        ok, detail = self._smoke(hdir, f"r{t}{vid}_smoke", ctx["inc_ev"])
        _write_json(vdir / "smoke.json", {"ok": ok, **detail})
        if not ok:
            return finish("smoke_fail", json.dumps(detail)[:600], edits, diff_path)
        _write_json(prep_path, {"commit": commit, "branch": branch, "edits": edits, "diff_path": diff_path,
                                "mechanism": prop.get("mechanism"), "harness_version": version})
        self.log(f"{vid}: {len(edits)} edit(s) on {[e['component'] for e in edits]} -> {commit[:12]}")
        return Draft(vid, edits, diff_path=diff_path, branch=branch, commit=commit, harness_version=version)

    def _smoke(self, hdir: Path, job: str, inc_ev: EvalResult) -> Tuple[bool, Dict[str, Any]]:
        try:
            instantiate(load_manifest(hdir))
        except Exception as exc:  # noqa: BLE001
            return False, {"stage": "load", "detail": f"{type(exc).__name__}: {exc}"[:600]}
        ids = self.domain.smoke_ids(inc_ev.per_task, n=self.cfg.smoke_n)
        if not ids:
            return True, {"stage": "load", "n": 0}
        out = self.jobs / job
        shutil.rmtree(out, ignore_errors=True)
        try:
            self._run_eval(hdir, self.domain.tasks_by_id(ids), 1, job, None)
        except Exception as exc:  # noqa: BLE001
            return False, {"stage": "smoke_run", "detail": f"{type(exc).__name__}: {exc}"[:600]}
        outcomes = load_outcomes(str(out))
        crashed = [o for o in outcomes
                   if o.missing or (o.status == "failed" and o.answer.startswith("[ERROR]"))]
        ok = not crashed and len(outcomes) == len(ids)
        return ok, {"stage": "smoke_run", "ids": ids, "n": len(outcomes),
                    "statuses": {o.task_id: o.status for o in outcomes},
                    "crashed": [{"task_id": o.task_id, "error": (o.error or o.answer)[:300]} for o in crashed]}

    # ------------------------------------------------------------- evaluate --
    def _evaluate(self, t: int, x: Draft, rdir: Path, tasks: Sequence[TaskSpec], inc_ev: EvalResult,
                  S_star: float, delta: float) -> None:
        job = f"r{t}{x.variant}"
        ep = rdir / x.variant / "eval.json"
        if ep.exists():
            stored = load_eval(ep)
            early = self._early_info(stored, tasks, inc_ev, S_star, delta)
            if not stored.extra.get("early_stopped") or early is not None:
                x.ev, x.early = stored, early
                self.log(f"{x.variant}: reusing eval.json")
                return
            # a partial evaluation whose stop is no longer exact (δ or S★ changed): complete it
            self.log(f"{x.variant}: stored early stop no longer exact; completing the evaluation")
            ep.unlink()
        hdir = self.repo.harness_dir(self.wt_root / job)
        es = EarlyStop(S_star=S_star, delta=delta, S_inc=inc_ev.S) if self.cfg.use_early_stop else None
        n_full = len(tasks) * self.cfg.k
        ev: Optional[EvalResult] = None
        for attempt in range(2):
            try:
                ev = self._measure(hdir, tasks, job, es, n_full, inc_ev, S_star, delta)
            except Exception as exc:  # noqa: BLE001 — an evaluation crash is an invalid measurement
                x.gate_failure, x.detail = "eval_invalid", f"evaluation crashed: {exc!r}"[:600]
                self.log(f"{x.variant}: {x.detail}")
                return
            if self._valid(ev, n_full):
                break
            self.log(f"{x.variant}: {ev.missing}/{n_full} trials missing (infrastructure)"
                     f"{'; retrying once' if attempt == 0 else ''}")
            if attempt == 0:
                self._clear_missing(self.jobs / job)
        assert ev is not None
        if not self._valid(ev, n_full):
            x.gate_failure = "eval_invalid"
            x.detail = f"{ev.missing}/{n_full} trials missing (infrastructure) after a retry"
            return
        save_eval(ep, ev)
        save_eval(self.eval_path(job), ev)
        x.ev = ev
        x.early = self._early_info(ev, tasks, inc_ev, S_star, delta)
        if x.early is not None:
            self.log(f"{x.variant}: stopped early after {ev.extra.get('trials_run')}/{n_full} trials "
                     f"(S' <= {x.early.S_upper:.4f} < floor)")

    def _measure(self, hdir: Path, tasks: Sequence[TaskSpec], job: str, es: Optional[EarlyStop],
                 n_full: int, inc_ev: EvalResult, S_star: float, delta: float) -> EvalResult:
        """One evaluation. An early stop is kept only when it cannot have been caused by
        infrastructure: the trials that ran pass the validity gate (against the full n_expected)
        and the stop condition re-checks with missing trials counted as unknown rather than as
        zeros. Otherwise the evaluation is completed (the runner reuses every finished trial)."""
        ev = self._run_eval(hdir, tasks, self.cfg.k, job, es)
        if not ev.extra.get("early_stopped"):
            return ev
        if not valid_measurement(replace(ev, n_expected=n_full), self.cfg.invalid_missing_frac):
            return ev  # invalid whatever the unrun trials do: the retry path handles it
        if self._early_info(ev, tasks, inc_ev, S_star, delta) is not None:
            return ev
        self.log(f"{job}: early stop not robust to the missing trials; completing the evaluation")
        return self._run_eval(hdir, tasks, self.cfg.k, job, None)

    def _valid(self, ev: EvalResult, n_full: int) -> bool:
        return valid_measurement(replace(ev, n_expected=max(ev.n_expected, n_full)), self.cfg.invalid_missing_frac)

    @staticmethod
    def _clear_missing(job_dir: Path) -> None:
        """Delete the trial directories of missing (infrastructure) trials so a retry re-runs them."""
        for path in sorted(job_dir.glob("*__*/outcome.json")):
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("missing"):
                    shutil.rmtree(path.parent, ignore_errors=True)
            except (OSError, ValueError):
                shutil.rmtree(path.parent, ignore_errors=True)

    def _early_info(self, ev: EvalResult, tasks: Sequence[TaskSpec], inc_ev: EvalResult,
                    S_star: float, delta: float) -> Optional[EarlyInfo]:
        """(A, B, R) of a partial evaluation when the exact stop condition holds for (S★, δ, Ŝ_t);
        None for a complete evaluation or when the condition fails. Missing (infrastructure)
        trials are counted as unknown — their weight moves from B to R — so an outage can never
        produce a floor rejection."""
        if not ev.extra.get("early_stopped"):
            return None
        weight = {task.id: task.weight for task in tasks}
        A = sum(r * w for tr in ev.per_task.values() for r, w in zip(tr.rewards, tr.weights))
        B = sum(w for tr in ev.per_task.values() for w in tr.weights)
        B -= sum(tr.missing * weight.get(tid, 1.0) for tid, tr in ev.per_task.items())
        W = sum(weight.values()) * self.cfg.k
        R = max(0.0, W - B)
        A = min(A, B)  # missing trials scored 0, so A is unchanged; guard float noise
        if not can_stop_exactly(A, B, R, S_star, delta, inc_ev.S):
            return None
        upper, _ = upper_lower(A, B, R)
        return EarlyInfo(A=A, B=B, R=R, S_partial=ev.S, S_upper=upper,
                         delta_S=early_stop_record_delta(A, B, R, inc_ev.S))

    # --------------------------------------------------------------- judge --
    def _judge(self, drafts: Sequence[Draft], inc_ev: EvalResult, S_star: float, delta: float,
               counts: Mapping[str, int], enabled: Sequence[str]) -> Tuple[Optional[Draft], List[Decision]]:
        """Algorithm 2 through ``rsi_math.select_round``. An early-stopped candidate is judged at
        its upper bound Ŝ_max (< S★ − δ, so the floor rejects it exactly) and its recorded ΔS is
        ``early_stop_record_delta``."""
        cands: List[Candidate] = []
        for x in drafts:
            ev = x.ev
            if ev is not None and ev.extra.get("early_stopped") and x.early is None:
                raise EvolveError(f"{x.variant}: a partial (early-stopped) evaluation cannot be judged "
                                  f"without an exact stop condition; re-evaluate it")
            if ev is not None and x.early is not None:
                ev = replace(ev, S=x.early.S_upper)
            cands.append(Candidate(x.variant, x.edits, ev, x.gate_failure if ev is None else None,
                                   x.detail, x.commit))
        win_c, decisions = select_round(cands, inc_ev, S_star, delta, self.cfg.params, counts,
                                        self.guard_fn, tie=self.cfg.mode_config.select_tie,
                                        enabled=enabled, eps=self.cfg.mode_config.float_eps)
        out: List[Decision] = []
        winner: Optional[Draft] = None
        for x, c, dec in zip(drafts, cands, decisions):
            if x.early is not None:
                if dec.reason_code != "floor":  # cannot happen: can_stop_exactly ⇒ Ŝ_max < S★ − δ
                    raise EvolveError(f"{x.variant}: early-stopped candidate not rejected by the floor")
                dec = replace(dec, delta_S=x.early.delta_S)
            out.append(dec)
            if win_c is not None and c is win_c:
                winner = x
        return winner, out

    def _outcome(self, x: Draft, dec: Decision, winner: Optional[Draft]) -> str:
        cand = Candidate(x.variant, (), x.ev, x.gate_failure if x.ev is None else None)
        return outcome_of(cand, cand if x is winner else None, dec)

    def _record(self, t: int, x: Draft, dec: Decision, winner: Optional[Draft], prefix: str = "") -> None:
        outcome = self._outcome(x, dec, winner)
        if x.ev is None:
            self.history.append_candidate(t, x.variant, x.edits, outcome, None, None, False, None, None,
                                          x.diff_path, x.detail)
            return
        self.history.append_candidate(t, x.variant, x.edits, outcome, dec.delta_S, dec.delta_C,
                                      outcome == "ACCEPTED", dec.S, dec.C, x.diff_path, prefix + dec.reason,
                                      early_stopped=x.early is not None,
                                      delta_C_partial=x.early is not None)
        self.log(f"{x.variant}: S={dec.S:.4f} dS={dec.delta_S:+.4f} dC={dec.delta_C:+.3f} "
                 f"nu={dec.novelty} -> {outcome}: {dec.reason}")

    def _write_decisions(self, rdir: Path, t: int, drafts: Sequence[Draft], decisions: Sequence[Decision],
                         winner: Optional[Draft], inc_ev: EvalResult, S_star: float, delta: float,
                         counts: Mapping[str, int], enabled: Sequence[str], *, readjudicated: bool) -> None:
        """decisions.json with every judgment input (05 §7.5) so the round can be re-adjudicated."""
        rows = []
        for x, dec in zip(drafts, decisions):
            row = dec.to_json()
            row["outcome"] = self._outcome(x, dec, winner)
            row["commit"] = x.commit
            row["early_stopped"] = x.early is not None
            row["delta_C_partial"] = x.early is not None
            if x.early is not None:
                row["early_stop"] = x.early.to_json()
            if x.ev is not None:
                row["S_measured"] = x.ev.S
                row["missing"] = x.ev.missing
                row["trials_run"] = x.ev.extra.get("trials_run")
            row["components"] = [e.get("component") for e in x.edits]
            rows.append(row)
        _write_json(rdir / "decisions.json", {
            "t": t, "readjudicated": readjudicated, "winner": winner.variant if winner else None,
            "inputs": {"S_star": S_star, "delta": delta,
                       "incumbent": {"job": inc_ev.job, "S": inc_ev.S, "C": inc_ev.C},
                       "accepted_counts": dict(counts), "params": asdict(self.cfg.params),
                       "tie": self.cfg.mode_config.select_tie, "enabled_kinds": list(enabled),
                       "guards": {"max_valid_rate_drop": self.cfg.max_valid_rate_drop,
                                  "max_nosub_rise": self.cfg.max_nosub_rise}},
            "decisions": rows})

    # ----------------------------------------------------- re-adjudication --
    def _variant_dirs(self, rdir: Path) -> List[Path]:
        if not rdir.exists():
            raise EvolveError(f"no round directory {rdir}")
        return sorted(p for p in rdir.iterdir() if p.is_dir() and len(p.name) == 1 and p.name in VARIANT_LABELS)

    def _stored_draft(self, vdir: Path) -> Draft:
        prep = _read_json(vdir / "prep.json", {}) or {}
        x = Draft(vdir.name, list(prep.get("edits") or []), diff_path=prep.get("diff_path"),
                  branch=prep.get("branch"), commit=prep.get("commit"),
                  harness_version=prep.get("harness_version"), gate_failure=prep.get("gate_failure"),
                  detail=prep.get("detail", ""))
        if (vdir / "eval.json").exists() and not x.gate_failure:
            x.ev = load_eval(vdir / "eval.json")
        elif not x.gate_failure:
            x.gate_failure = "eval_invalid"
        return x

    def _round_inputs(self, t: int) -> Tuple[Dict[str, Any], Dict[str, Any], str, EvalResult, float]:
        fr = self.frontier()
        traj = sorted(fr["trajectory"], key=lambda e: int(e["t"]))
        if len(traj) > t + 2:
            raise EvolveError(f"rounds after {t} exist in the frontier; remove them first")
        if len(traj) < t + 1:
            raise EvolveError(f"round {t} has no incumbent in the trajectory")
        inc_entry = traj[t]
        inc_job = inc_entry.get("job") or ("base" if t == 0 else None)
        if not inc_job:
            raise EvolveError(f"trajectory[{t}] has no job; cannot locate H_{t}'s evaluation")
        inc_ev = load_eval(self.eval_path(inc_job))
        S_star = max(float(e["S"]) for e in traj[: t + 1])
        return fr, inc_entry, inc_job, inc_ev, S_star

    def readjudicate(self, t: int) -> Dict[str, Any]:
        """Re-run Algorithm 2 on the STORED measurements of round t (after a change of δ or of the
        acceptance weights). No evaluation is spent. N_t(ℓ) is recounted over records with t_i < t.
        Early-stopped candidates are re-checked: if the stop condition no longer holds under the
        new δ / S★ the round is NOT re-decided (NeedsReevaluation) — never guessed."""
        fr, inc_entry, inc_job, inc_ev, S_star = self._round_inputs(t)
        delta = self.delta()
        rdir = self.run_dir / f"r{t}"
        tasks = self.domain.evolve_tasks()
        drafts = [self._stored_draft(v) for v in self._variant_dirs(rdir)]
        needs = []
        for x in drafts:
            if x.ev is not None and x.ev.extra.get("early_stopped"):
                x.early = self._early_info(x.ev, tasks, inc_ev, S_star, delta)
                if x.early is None:
                    needs.append(x.variant)
        if needs:
            _write_json(rdir / "reevaluate_needed.json", {
                "t": t, "variants": needs, "delta": delta, "S_star": S_star, "S_inc": inc_ev.S,
                "reason": "early stop no longer exact under the new delta / S_star"})
            raise NeedsReevaluation(t, needs)
        (rdir / "reevaluate_needed.json").unlink(missing_ok=True)
        inc_manifest_kinds = self._enabled_at(str(inc_entry["commit"]))
        counts = self.history.accepted_counts(before_t=t)
        winner, decisions = self._judge(drafts, inc_ev, S_star, delta, counts, inc_manifest_kinds)
        self._write_decisions(rdir, t, drafts, decisions, winner, inc_ev, S_star, delta, counts,
                              inc_manifest_kinds, readjudicated=True)
        self.history.replace_round(t)
        for x, dec in zip(drafts, decisions):
            self._record(t, x, dec, winner, prefix=f"[re-adjudicated delta={delta:.5f}] ")
        if winner is not None and winner.ev is not None and winner.commit:
            if not self.repo.is_ancestor(str(inc_entry["commit"]), winner.commit):
                raise EvolveError(f"{winner.commit} does not descend from H_{t} {inc_entry['commit']}")
            new_inc = F.incumbent_entry(t + 1, winner.commit, self.repo.tree_hash(winner.commit),
                                        winner.harness_version or "", f"r{t}{winner.variant}",
                                        winner.ev, winner.variant)
        else:
            commit = str(inc_entry["commit"])
            new_inc = F.incumbent_entry(t + 1, commit, self.repo.tree_hash(commit),
                                        str(inc_entry.get("harness_version") or ""), inc_job, inc_ev)
        fr2 = F.settle(fr, t, new_inc, S_star=S_star)
        F.save(self.frontier_path, fr2)
        self.repo.update_ref(self.branch, str(new_inc["commit"]))
        self.log(f"re-adjudicated r{t} (delta={delta:.5f}): "
                 + (f"ACCEPTED {winner.variant} S={new_inc['S']:.4f}" if winner else "no admissible candidate"))
        return {"winner": winner.variant if winner else None,
                "decisions": [dec.to_json() for dec in decisions]}

    def _enabled_at(self, commit: str) -> List[str]:
        wt = self.repo.worktree_detached(self.wt_root / f"kinds_{commit[:12]}", commit)
        try:
            return self.enabled_kinds(load_manifest(self.repo.harness_dir(wt)))
        finally:
            self.repo.worktree_remove(wt)

    def reevaluate(self, t: int, variants: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Re-measure stored (committed) candidates of round t — invalid, corrupted by an
        infrastructure failure, or early-stopped where the stop is no longer exact — then
        re-adjudicate. Finished non-missing trials are reused."""
        _, _, _, inc_ev, S_star = self._round_inputs(t)
        delta = self.delta()
        rdir = self.run_dir / f"r{t}"
        tasks = self.domain.evolve_tasks()
        for vdir in self._variant_dirs(rdir):
            if variants and vdir.name not in variants:
                continue
            prep = _read_json(vdir / "prep.json", {}) or {}
            branch = str(prep.get("branch") or "")
            if not prep.get("commit") or not self.repo.branch_exists(branch):
                self.log(f"r{t}{vdir.name}: no committed candidate to re-evaluate")
                continue
            (vdir / "eval.json").unlink(missing_ok=True)
            job = f"r{t}{vdir.name}"
            self._clear_missing(self.jobs / job)
            wt = self.repo.worktree_checkout(self.wt_root / job, branch)
            try:
                x = Draft(vdir.name, list(prep.get("edits") or []), diff_path=prep.get("diff_path"),
                          branch=branch, commit=prep["commit"], harness_version=prep.get("harness_version"))
                self._evaluate(t, x, rdir, tasks, inc_ev, S_star, delta)
                self.log(f"r{t}{vdir.name}: re-evaluated -> "
                         + (f"S={x.ev.S:.4f} C={x.ev.C} missing={x.ev.missing}" if x.ev is not None
                            else f"{x.gate_failure}: {x.detail}"))
            finally:
                self.repo.worktree_remove(wt)
        return self.readjudicate(t)
