"""One Dream cycle (L2): dreaming-based policy improvement over the world pool (03 §3.6–3.7).

``DreamCycle(cycle_dir, worlds, incumbent_source, llm=...)``:

1. π^0 = the current policy (source). Every version is loaded through the
   sandbox and evaluated on the *development* worlds (Eq.1 at its baked
   default β + the β sweep), archived as
   ``history/r####_<origin>/{policy.py, eval.json,
   proposal_results/beta_sweep.json, proposal_results/policy_execution_traces.jsonl}``.
   The parallel-refine floor is evaluated into ``history/baseline/``.
2. The cross-cycle default-β reference is computed once from the live
   manifests and the sweeps (π^0's sweep paired with this cycle's iteration)
   via ``rsi_math.next_default_beta`` and handed to the development agent; each
   developed version's baked β is recorded against it.
3. π^1..π^{M−1} come from the policy-development agent (E4: M = 4, E10: M−1
   development steps), each seeing every earlier version of the cycle.
4. Selection on the *selection* worlds (paper mode and single-task pools: the
   same worlds): ``m★ = rsi_math.select_policy`` over V^m (xgen tie → π^0, E11;
   versions that failed validation or errored are ineligible) and
   ``rsi_math.assert_non_decreasing``.
5. Online confirmation (P9, R-9): when m★ ≠ 0 a caller-supplied hook decides
   (the lead wires it to RRSI's floor + cost rule). A veto keeps π^0; without
   a hook the winner stays ``pending_confirmation`` (xgen) — paper mode
   promotes directly.
6. Outputs: ``cycle_manifest.json`` (all V, m★, status, β reference,
   confirmation), ``promoted_policy.py`` and
   ``_current/live_cycle_manifest.json`` for the next live cycle (baked β and
   ``plan_grid`` of the promoted policy).

Every step writes its files atomically and is skipped on a rerun when its
outputs exist, so an interrupted cycle resumes without re-asking the LLM or
the confirmation hook.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xgen_rsi.explore.api import GridPlan, GridPlanningContext, LiveCycleManifest
from xgen_rsi.explore.policies import builtin_policy_source
from xgen_rsi.rsi_math import (
    BetaDecision,
    BetaSweep,
    assert_non_decreasing,
    next_default_beta,
    select_policy,
)
from xgen_rsi.rsi_math.modes import Mode, ModeConfig

from .develop import PolicyDeveloper, VersionRecord, run_plan_grid
from .evaluator import EvalConfig, PolicyEvaluation, evaluate_policy, sweep_points_from_json
from .manifest import live_cycles, write_live_manifest
from .sandbox import LoadedPolicy, PolicyRejected, load_policy
from .world import World, WorldPool


@dataclass(frozen=True)
class ConfirmationRequest:
    """What the online-confirmation hook gets about the replay winner m★ ≠ 0."""

    cycle_dir: str
    iteration: int
    candidate_index: int
    candidate_label: str
    candidate_source: str
    candidate_path: str
    candidate_V: float
    incumbent_V: float
    candidate_beta: float | None
    incumbent_beta: float | None
    candidate_summary: Mapping[str, Any]
    incumbent_summary: Mapping[str, Any]


@dataclass(frozen=True)
class ConfirmationResult:
    approved: bool
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"approved": self.approved, "reason": self.reason, "details": dict(self.details)}


OnlineConfirm = Callable[[ConfirmationRequest], "ConfirmationResult | bool | Mapping[str, Any]"]


@dataclass(frozen=True)
class CycleConfig:
    """Cycle settings (E4 M = 4; mode switch of 33 §7)."""

    M: int = 4
    mode: Mode = "xgen"
    eval: EvalConfig | None = None
    selection_fraction: float = 0.5
    max_repairs: int = 2
    require_confirmation: bool | None = None
    timeout: float | None = 10.0
    evaluate_baseline: bool = True
    beta_step: float = 0.15
    plateau_delta: float = 0.0
    beta_window: int = 2
    max_trace_chars: int = 12000

    def __post_init__(self) -> None:
        if self.M < 1:
            raise ValueError("M must be >= 1")
        if self.eval is None:
            object.__setattr__(self, "eval", EvalConfig(mode=self.mode, timeout=self.timeout))
        elif self.eval.mode != self.mode:
            raise ValueError("eval.mode must match the cycle mode")
        if self.require_confirmation is None:
            object.__setattr__(self, "require_confirmation", self.mode == "xgen")


@dataclass(frozen=True)
class CycleResult:
    iteration: int
    status: str
    m_star: int
    promoted_index: int
    promoted_label: str
    promoted_source: str
    V_selection: tuple[float | None, ...]
    V_dev: tuple[float | None, ...]
    eligible: tuple[bool, ...]
    labels: tuple[str, ...]
    beta_decision: Mapping[str, Any]
    next_beta: float | None
    next_plan: Mapping[str, Any] | None
    confirmation: Mapping[str, Any] | None
    pi0_sweep_points: tuple[tuple[float, float, float, float], ...]
    cycle_dir: str

    @property
    def pi0_sweep(self) -> BetaSweep:
        """π^0's β sweep paired with this cycle's live iteration (archive for the next β rule)."""
        return BetaSweep(iteration=self.iteration, points=self.pi0_sweep_points)

    def to_json(self) -> dict[str, Any]:
        return {"complete": True, "iteration": self.iteration, "status": self.status,
                "m_star": self.m_star, "promoted_index": self.promoted_index,
                "promoted_label": self.promoted_label, "V_selection": list(self.V_selection),
                "V_dev": list(self.V_dev), "eligible": list(self.eligible),
                "labels": list(self.labels), "beta_decision": dict(self.beta_decision),
                "next_beta": self.next_beta, "next_plan": self.next_plan,
                "confirmation": self.confirmation,
                "pi0_sweep_points": [list(p) for p in self.pi0_sweep_points],
                "pareto_auc_version": "v1"}

    @classmethod
    def from_json(cls, d: Mapping[str, Any], *, cycle_dir: str, promoted_source: str) -> CycleResult:
        return cls(iteration=int(d["iteration"]), status=str(d["status"]),
                   m_star=int(d["m_star"]), promoted_index=int(d["promoted_index"]),
                   promoted_label=str(d["promoted_label"]), promoted_source=promoted_source,
                   V_selection=tuple(d["V_selection"]), V_dev=tuple(d["V_dev"]),
                   eligible=tuple(bool(x) for x in d["eligible"]), labels=tuple(d["labels"]),
                   beta_decision=d.get("beta_decision") or {}, next_beta=d.get("next_beta"),
                   next_plan=d.get("next_plan"), confirmation=d.get("confirmation"),
                   pi0_sweep_points=tuple(tuple(p) for p in d.get("pi0_sweep_points") or ()),
                   cycle_dir=cycle_dir)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_json(path: Path, obj: Any) -> None:
    _write(path, json.dumps(_clean(obj), indent=1, default=str))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _clean(o: Any) -> Any:
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, Mapping):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _compact_trace(line: str, max_rounds: int = 6) -> str:
    """A short per-episode digest of a ``policy_execution_traces.jsonl`` line for the prompt."""
    try:
        d = json.loads(line)
    except ValueError:
        return line[:400]
    rounds = []
    for r in (d.get("decision_rounds") or [])[:max_rounds]:
        rev = [(x.get("cell_id"), x.get("score"), x.get("fail_class")) for x in r.get("revealed") or []]
        rounds.append({"k": r.get("round"), "n_legal": (r.get("prefix") or {}).get("n_legal"),
                       "batch": r.get("batch"), "revealed": rev})
    return json.dumps({"world": d.get("world_id"), "role": d.get("role"), "beta": d.get("beta"),
                       "stop": d.get("stop_reason"), "probes": d.get("probes"),
                       "rounds": d.get("rounds"), "batch_sizes": d.get("batch_sizes"),
                       "first_rounds": rounds}, default=str)


class DreamCycle:
    """π^0..π^{M−1} → replay evaluation → argmax V → online confirmation → β reference."""

    def __init__(self, cycle_dir: str | os.PathLike[str], worlds: WorldPool | Sequence[World],
                 incumbent_source: str, *, llm: Any = None, iteration: int = 0,
                 config: CycleConfig | None = None, online_confirm: OnlineConfirm | None = None,
                 live_manifests: Sequence[LiveCycleManifest] = (),
                 prior_sweeps: Sequence[BetaSweep] = (),
                 baseline_source: str | None = None,
                 planning_context: GridPlanningContext | None = None) -> None:
        self.dir = Path(cycle_dir)
        self.pool = worlds if isinstance(worlds, WorldPool) else WorldPool(worlds)
        if len(self.pool) == 0:
            raise ValueError("a Dream cycle needs at least one world")
        self.incumbent_source = incumbent_source
        self.llm = llm
        self.iteration = int(iteration)
        self.cfg = config or CycleConfig()
        self.confirm = online_confirm
        self.live_manifests = tuple(sorted(live_manifests, key=lambda m: m.iteration))
        self.prior_sweeps = tuple(prior_sweeps)
        self.baseline_source = (builtin_policy_source("parallel_refine")
                                if baseline_source is None else baseline_source)
        self.planning_context = planning_context
        ev = self.cfg.eval
        assert ev is not None
        self.eval_cfg: EvalConfig = ev
        self.mode_cfg = ModeConfig.for_mode(self.cfg.mode)
        split = self.pool.split(self.cfg.mode, selection_fraction=self.cfg.selection_fraction)
        self.dev = split.dev
        self.sel = split.selection
        self.shared = split.shared
        ids: set[str] = set()
        scores: set[float] = set()
        for w in self.pool:
            ids.update(w.cell_ids)
            ids.update(w.node_ids)
            scores.update(w.scores())
        self.cell_ids = tuple(sorted(ids))
        self.score_values = tuple(sorted(scores))

    # ------------------------------------------------------------------ io --

    @property
    def history_dir(self) -> Path:
        return self.dir / "history"

    def _vdir(self, m: int) -> Path:
        return self.history_dir / f"r{m:04d}_{'pi0' if m == 0 else 'dev'}"

    def _load(self, source: str) -> LoadedPolicy:
        return load_policy(source, timeout=self.cfg.timeout, cell_ids=self.cell_ids,
                           score_values=self.score_values, require=("solve",))

    def _evaluate_into(self, vdir: Path, loaded: LoadedPolicy, label: str) -> dict[str, Any]:
        """Dev evaluation (+ selection evaluation when the split is not shared); resume-safe."""
        dev_eval_path = vdir / "eval.json"
        if dev_eval_path.exists() and (vdir / "proposal_results" / "beta_sweep.json").exists():
            dev = _read_json(dev_eval_path)
        else:
            ev = evaluate_policy(loaded, list(self.dev), self.eval_cfg, label=label,
                                 history=self.live_manifests, out_dir=vdir)
            dev = ev.summary()
        sel_path = vdir / "eval_selection.json"
        if self.shared:
            sel = {"V": dev["V"], "eligible": dev["eligible"], "errors": dev["errors"],
                   "shared": True}
        elif sel_path.exists():
            sel = _read_json(sel_path)
        else:
            ev2: PolicyEvaluation = evaluate_policy(loaded, list(self.sel), self.eval_cfg,
                                                    label=label, history=self.live_manifests,
                                                    sweep=False)
            sel = {"V": ev2.V, "V_by_world": ev2.V_by_world, "eligible": ev2.eligible,
                   "errors": ev2.errors, "baked_beta": ev2.baked_beta, "shared": False}
            _write_json(sel_path, sel)
        return {"dev": dev, "selection": sel}

    def _record(self, m: int) -> VersionRecord:
        vdir = self._vdir(m)
        status = _read_json(vdir / "status.json")
        source = (vdir / "policy.py").read_text(encoding="utf-8") if (vdir / "policy.py").exists() else ""
        if status["state"] != "evaluated":
            return VersionRecord(index=m, label=vdir.name, source=source,
                                 origin="incumbent" if m == 0 else "developed", eligible=False,
                                 note=status.get("reason", "rejected"))
        ev = _read_json(vdir / "eval.json")
        sweep_path = vdir / "proposal_results" / "beta_sweep.json"
        sweep = _read_json(sweep_path) if sweep_path.exists() else None
        tr_path = vdir / "proposal_results" / "policy_execution_traces.jsonl"
        traces = [_compact_trace(x) for x in tr_path.read_text(encoding="utf-8").splitlines()
                  if x.strip()] if tr_path.exists() else []
        return VersionRecord(index=m, label=vdir.name, source=source,
                             origin="incumbent" if m == 0 else "developed",
                             eligible=bool(ev.get("eligible")), V=ev.get("V"),
                             baked_beta=ev.get("baked_beta"), sweep=sweep, traces=traces,
                             note="; ".join(ev.get("errors") or [])[:300])

    # ----------------------------------------------------------------- run --

    def run(self) -> CycleResult:
        manifest_path = self.dir / "cycle_manifest.json"
        if manifest_path.exists():
            d = _read_json(manifest_path)
            if d.get("complete"):
                src = (self.dir / "promoted_policy.py").read_text(encoding="utf-8")
                return CycleResult.from_json(d, cycle_dir=str(self.dir), promoted_source=src)
        self.dir.mkdir(parents=True, exist_ok=True)
        if not (self.dir / "worlds.json").exists():
            self.pool.save(self.dir / "worlds.json")
        _write_json(self.dir / "split.json", {"dev": list(self.dev.ids),
                                              "selection": list(self.sel.ids),
                                              "shared": self.shared, "mode": self.cfg.mode})
        baseline = self._baseline()
        evals: dict[int, dict[str, Any]] = {}
        evals[0] = self._version0()
        decision = self._beta_decision(evals[0])
        developer = (PolicyDeveloper(self.llm, max_repairs=self.cfg.max_repairs,
                                     history_dir="history", timeout=self.cfg.timeout,
                                     max_trace_chars=self.cfg.max_trace_chars)
                     if self.llm is not None else None)
        for m in range(1, self.cfg.M):
            evals[m] = self._developed(m, developer, baseline, decision)
        return self._select_and_promote(evals, decision)

    def _baseline(self) -> VersionRecord | None:
        if not (self.cfg.evaluate_baseline and self.baseline_source):
            return None
        bdir = self.history_dir / "baseline"
        _write(bdir / "policy.py", self.baseline_source)
        if not (bdir / "eval.json").exists():
            loaded = self._load(self.baseline_source)
            evaluate_policy(loaded, list(self.dev), self.eval_cfg, label="baseline",
                            history=self.live_manifests, out_dir=bdir)
        ev = _read_json(bdir / "eval.json")
        sweep = _read_json(bdir / "proposal_results" / "beta_sweep.json")
        return VersionRecord(index=-1, label="baseline", source=self.baseline_source,
                             origin="baseline", eligible=bool(ev.get("eligible")),
                             V=ev.get("V"), baked_beta=ev.get("baked_beta"), sweep=sweep)

    def _version0(self) -> dict[str, Any]:
        vdir = self._vdir(0)
        status_path = vdir / "status.json"
        if status_path.exists() and _read_json(status_path)["state"] == "evaluated":
            return {"dev": _read_json(vdir / "eval.json"),
                    "selection": self._sel_of(vdir)}
        _write(vdir / "policy.py", self.incumbent_source)
        try:
            loaded = self._load(self.incumbent_source)
        except PolicyRejected as exc:
            raise ValueError(f"incumbent policy π^0 failed the sandbox: {exc}") from exc
        out = self._evaluate_into(vdir, loaded, vdir.name)
        _write_json(status_path, {"state": "evaluated", "origin": "incumbent"})
        return out

    def _sel_of(self, vdir: Path) -> dict[str, Any]:
        if self.shared:
            dev = _read_json(vdir / "eval.json")
            return {"V": dev["V"], "eligible": dev["eligible"], "errors": dev["errors"],
                    "shared": True}
        return _read_json(vdir / "eval_selection.json")

    def _beta_decision(self, pi0: Mapping[str, Any]) -> BetaDecision:
        path = self.dir / "beta_decision.json"
        if path.exists():
            d = _read_json(path)
            return BetaDecision(beta=float(d["beta"]), branch=str(d["branch"]),
                                reason=str(d["reason"]))
        sweep = _read_json(self._vdir(0) / "proposal_results" / "beta_sweep.json")
        sweeps = [*self.prior_sweeps,
                  BetaSweep(iteration=self.iteration, points=sweep_points_from_json(sweep))]
        decision = next_default_beta(live_cycles(self.live_manifests), sweeps,
                                     step=self.cfg.beta_step,
                                     plateau_delta=self.cfg.plateau_delta,
                                     window=self.cfg.beta_window)
        _write_json(path, {"beta": decision.beta, "branch": decision.branch,
                           "reason": decision.reason,
                           "pi0_baked_beta": pi0["dev"].get("baked_beta")})
        return decision

    def _developed(self, m: int, developer: PolicyDeveloper | None,
                   baseline: VersionRecord | None, decision: BetaDecision) -> dict[str, Any]:
        vdir = self._vdir(m)
        status_path = vdir / "status.json"
        status = _read_json(status_path) if status_path.exists() else {"state": "new"}
        if status["state"] == "evaluated":
            return {"dev": _read_json(vdir / "eval.json"), "selection": self._sel_of(vdir)}
        if status["state"] == "rejected":
            return {"rejected": status.get("reason", "rejected")}
        if status["state"] != "validated":
            if developer is None:
                _write_json(status_path, {"state": "rejected", "reason": "no development LLM"})
                return {"rejected": "no development LLM"}
            history = [self._record(i) for i in range(m)]
            res = developer.develop(history, worlds=list(self.dev),
                                    W=self.eval_cfg.W_for(self.dev[0]), baseline=baseline,
                                    live_manifests=self.live_manifests, beta_decision=decision,
                                    cell_ids=self.cell_ids, score_values=self.score_values,
                                    root_policy=self.eval_cfg.root_policy or "choose",
                                    root_multi=bool(self.eval_cfg.root_multi))
            for a in res.attempts:
                j = a["attempt"]
                _write(vdir / "develop" / f"attempt_{j}_prompt.txt", a["prompt"])
                _write(vdir / "develop" / f"attempt_{j}_response.txt", a.get("response") or "")
                _write_json(vdir / "develop" / f"attempt_{j}_report.json", a.get("report"))
            if not res.ok or res.source is None:
                last = res.attempts[-1]["report"] if res.attempts else {"problems": ["no attempt"]}
                reason = "development failed: " + "; ".join(last.get("problems") or [])[:500]
                _write_json(status_path, {"state": "rejected", "reason": reason})
                return {"rejected": reason}
            _write(vdir / "policy.py", res.source)
            _write_json(status_path, {"state": "validated", "origin": "developed"})
        source = (vdir / "policy.py").read_text(encoding="utf-8")
        try:
            loaded = self._load(source)
        except PolicyRejected as exc:
            _write_json(status_path, {"state": "rejected", "reason": str(exc)[:500]})
            return {"rejected": str(exc)}
        out = self._evaluate_into(vdir, loaded, vdir.name)
        baked = out["dev"].get("baked_beta")
        check = None
        if isinstance(baked, (int, float)):
            dev_ = abs(float(baked) - decision.beta)
            check = {"baked": baked, "rule": decision.beta, "deviation": dev_,
                     "within_step": dev_ <= self.cfg.beta_step + 1e-9}
        _write_json(status_path, {"state": "evaluated", "origin": "developed",
                                  "beta_check": check})
        return out

    def _select_and_promote(self, evals: Mapping[int, Mapping[str, Any]],
                            decision: BetaDecision) -> CycleResult:
        M = self.cfg.M
        labels = tuple(self._vdir(m).name for m in range(M))
        V_sel: list[float | None] = []
        V_dev: list[float | None] = []
        eligible: list[bool] = []
        for m in range(M):
            e = evals[m]
            if "rejected" in e:
                V_sel.append(None)
                V_dev.append(None)
                eligible.append(False)
                continue
            V_dev.append(float(e["dev"]["V"]))
            V_sel.append(float(e["selection"]["V"]))
            eligible.append(bool(e["dev"]["eligible"]) and bool(e["selection"]["eligible"]))
        if V_sel[0] is None:
            raise RuntimeError("π^0 has no selection value")
        vec = [float(V_sel[0])] + [
            (float(v) if (v is not None and ok) else float("-inf"))
            for v, ok in zip(V_sel[1:], eligible[1:])]
        m_star = select_policy(vec, tie=self.mode_cfg.policy_tie)
        assert_non_decreasing(vec, m_star)
        confirmation: dict[str, Any] | None = None
        if m_star == 0:
            status, promoted = "kept", 0
        elif not self.cfg.require_confirmation:
            status, promoted = "promoted", m_star
        else:
            confirmation = self._confirm(m_star, evals, vec)
            if confirmation is None:
                status, promoted = "pending_confirmation", 0
            elif confirmation["approved"]:
                status, promoted = "promoted", m_star
            else:
                status, promoted = "vetoed", 0
        pdir = self._vdir(promoted)
        source = (pdir / "policy.py").read_text(encoding="utf-8")
        _write(self.dir / "promoted_policy.py", source)
        next_beta = evals[promoted]["dev"].get("baked_beta")
        plan = self._next_plan(source)
        next_plan = None if plan is None else {"branch_count": plan.branch_count,
                                               "refine_count": plan.refine_count,
                                               "reason": plan.reason}
        if plan is not None:
            write_live_manifest(self.dir / "_current", LiveCycleManifest(
                iteration=self.iteration + 1,
                beta=float(next_beta) if isinstance(next_beta, (int, float)) else decision.beta,
                best_score=None, planned_branch_count=plan.branch_count,
                planned_refine_count=plan.refine_count, effective_branch_count=plan.branch_count,
                effective_refine_count=plan.refine_count,
                W=int(self.planning_ctx().worker_cap), policy_version=pdir.name,
                plan_reason=plan.reason))
        sweep0 = _read_json(self._vdir(0) / "proposal_results" / "beta_sweep.json")
        result = CycleResult(
            iteration=self.iteration, status=status, m_star=m_star, promoted_index=promoted,
            promoted_label=pdir.name, promoted_source=source, V_selection=tuple(V_sel),
            V_dev=tuple(V_dev), eligible=tuple(eligible), labels=labels,
            beta_decision={"beta": decision.beta, "branch": decision.branch,
                           "reason": decision.reason},
            next_beta=next_beta if isinstance(next_beta, (int, float)) else None,
            next_plan=next_plan, confirmation=confirmation,
            pi0_sweep_points=sweep_points_from_json(sweep0), cycle_dir=str(self.dir))
        manifest = result.to_json()
        manifest.update({"mode": self.cfg.mode, "M": M, "shared_worlds": self.shared,
                         "dev_worlds": list(self.dev.ids), "selection_worlds": list(self.sel.ids),
                         "selection_vector": vec, "eval_config": self.eval_cfg.to_json()})
        _write_json(self.dir / "cycle_manifest.json", manifest)
        return result

    def _confirm(self, m_star: int, evals: Mapping[int, Mapping[str, Any]],
                 vec: Sequence[float]) -> dict[str, Any] | None:
        path = self.dir / "confirmation.json"
        if path.exists():
            return _read_json(path)
        if self.confirm is None:
            return None
        vdir = self._vdir(m_star)
        req = ConfirmationRequest(
            cycle_dir=str(self.dir), iteration=self.iteration, candidate_index=m_star,
            candidate_label=vdir.name,
            candidate_source=(vdir / "policy.py").read_text(encoding="utf-8"),
            candidate_path=str(vdir / "policy.py"), candidate_V=float(vec[m_star]),
            incumbent_V=float(vec[0]), candidate_beta=evals[m_star]["dev"].get("baked_beta"),
            incumbent_beta=evals[0]["dev"].get("baked_beta"),
            candidate_summary=evals[m_star]["dev"], incumbent_summary=evals[0]["dev"])
        raw = self.confirm(req)
        if isinstance(raw, ConfirmationResult):
            out = raw.to_json()
        elif isinstance(raw, Mapping):
            out = {"approved": bool(raw.get("approved")), "reason": str(raw.get("reason", "")),
                   "details": dict(raw.get("details") or {})}
        else:
            out = {"approved": bool(raw), "reason": "", "details": {}}
        out["candidate_index"] = m_star
        _write_json(path, out)
        return out

    def planning_ctx(self) -> GridPlanningContext:
        """Context for the next live grid (``planning_context`` or one derived from the pool)."""
        if self.planning_context is not None:
            ctx = self.planning_context
            return GridPlanningContext(
                hard_max_branch_count=ctx.hard_max_branch_count,
                hard_max_refine_count=ctx.hard_max_refine_count, worker_cap=ctx.worker_cap,
                fallback_branch_count=ctx.fallback_branch_count,
                fallback_refine_count=ctx.fallback_refine_count, trace_branch_count=None,
                trace_refine_count=None, history=self.live_manifests)
        B = max([1, *(w.trace_branch_count for w in self.pool)])
        R = max([0, *(w.trace_refine_count for w in self.pool)])
        fb_B, fb_R = B, R
        if self.live_manifests:
            last = self.live_manifests[-1]
            fb_B = max(1, last.effective_branch_count or B)
            fb_R = max(0, last.effective_refine_count or R)
        return GridPlanningContext(
            hard_max_branch_count=max(B, fb_B), hard_max_refine_count=max(R, fb_R),
            worker_cap=self.eval_cfg.W_for(self.pool[0]), fallback_branch_count=fb_B,
            fallback_refine_count=fb_R, history=self.live_manifests)

    def _next_plan(self, source: str) -> GridPlan | None:
        try:
            loaded = self._load(source)
        except PolicyRejected:
            return None
        return run_plan_grid(loaded, self.planning_ctx())


__all__ = ["ConfirmationRequest", "ConfirmationResult", "CycleConfig", "CycleResult",
           "DreamCycle", "OnlineConfirm"]
