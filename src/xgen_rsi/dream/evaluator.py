"""Replay evaluator of one policy version (04 §5–6, 05 §4.2–4.3, 33 §5).

For a policy version and a set of worlds it computes

- **Eq.1** ``V_i^m`` at the version's *baked default β* (config ``{}``) via
  ``rsi_math.replay_value`` with the world's normalizer (xgen: s̃ per E8;
  paper: raw scores) and ``V^m = rsi_math.mean_value`` — the selection
  objective (04 §5.4);
- the **β sweep** over :data:`DEFAULT_BETA_GRID` (E7): per world and β a point
  ``(u, q)`` with ``u = N / N_max`` and ``q`` = final best-so-far s̃
  (root included, E15), then ``rsi_math.pareto_auc_v1``,
  ``rsi_math.parallel_penalty`` and ``rsi_math.pareto_reward`` with λ = 0.1
  (E6) — the development feedback;
- per-β frontier points ``(β, attainment, work, reward)`` (the input of the
  cross-cycle β rule), where the per-β reward is the same Appendix B formula
  restricted to that single β;
- the anytime AUC (diagnostic, ``rsi_math.anytime_auc``).

E9 defaults: ``β_cost = 0.25 / N_ref`` (N_ref = 110) and ``β_par = 0.05 / W``.

Archive files (names from the paper's Appendix B):
``proposal_results/beta_sweep.json`` and
``proposal_results/policy_execution_traces.jsonl`` (one replay episode per
(frozen trace, β), with the per-round prefix summary, batch and revealed
outcomes), plus ``eval.json``.

An out-of-support or invalid grid plan earns no replay reward: the episode is
empty, so ``V_i`` is the empty-replay value s̃_r (= 0 under E8 normalization;
raw ``s_r`` in paper mode) and the sweep point is ``(0, s̃_r)``.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from xgen_rsi.explore.api import LiveCycleManifest
from xgen_rsi.rsi_math import (
    anytime_auc,
    episode_penalty,
    mean_value,
    parallel_penalty,
    pareto_auc_v1,
    pareto_reward,
    replay_value,
)
from xgen_rsi.rsi_math.modes import Mode, ModeConfig, RootPolicy

from .replay import ReplayRun, run_episode
from .world import World

DEFAULT_BETA_GRID: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
PARETO_AUC_VERSION = "v1"


@dataclass(frozen=True)
class EvalConfig:
    """Evaluator settings; ``None`` fields resolve from the mode (33 §7) or E6/E7/E9."""

    mode: Mode = "xgen"
    W: int | None = None
    default_W: int = 4
    beta_grid: tuple[float, ...] = DEFAULT_BETA_GRID
    lam: float = 0.1
    beta_cost: float | None = None
    beta_par: float | None = None
    n_ref: int = 110
    root_policy: RootPolicy | None = None
    root_multi: bool | None = None
    apply_plan: bool | None = None
    hard_max_branch_count: int | None = None
    hard_max_refine_count: int | None = None
    K2: int | None = None
    timeout: float | None | str = "default"
    """Per-episode wall-clock limit; ``"default"`` = the loaded policy's own limit."""

    def __post_init__(self) -> None:
        if self.lam < 0:
            raise ValueError("lambda must be >= 0")
        if any(not 0.0 <= b <= 1.0 for b in self.beta_grid):
            raise ValueError("beta grid values must lie in [0, 1]")
        mc = ModeConfig.for_mode(self.mode)
        if self.root_policy is None:
            object.__setattr__(self, "root_policy", mc.root_policy)
        if self.root_multi is None:
            object.__setattr__(self, "root_multi", mc.root_multi)
        if self.apply_plan is None:
            object.__setattr__(self, "apply_plan", self.mode == "xgen")

    def W_for(self, world: World) -> int:
        """Worker count of a world: the configured W, else the world's recorded W, else default."""
        if self.W is not None:
            return int(self.W)
        w = world.meta.get("W")
        return int(w) if isinstance(w, int) and w >= 1 else int(self.default_W)

    def betas_eq1(self, W: int) -> tuple[float, float]:
        """(β_cost, β_par) of D-Eq1; E9 defaults 0.25/N_ref and 0.05/W."""
        bc = self.beta_cost if self.beta_cost is not None else 0.25 / self.n_ref
        bp = self.beta_par if self.beta_par is not None else 0.05 / W
        return float(bc), float(bp)

    def to_json(self) -> dict[str, Any]:
        return {"mode": self.mode, "W": self.W, "default_W": self.default_W,
                "beta_grid": list(self.beta_grid), "lambda": self.lam,
                "beta_cost": self.beta_cost, "beta_par": self.beta_par, "n_ref": self.n_ref,
                "root_policy": self.root_policy, "root_multi": self.root_multi,
                "apply_plan": self.apply_plan, "K2": self.K2, "timeout": self.timeout}


def _q(run: ReplayRun, world: World) -> float:
    """Final best-so-far s̃ of an episode (root included, E15)."""
    norm = world.normalizer("xgen")
    vals = [norm(s) for s in run.episode.revealed_scores]
    if world.root_score is not None:
        vals.append(norm(world.root_score))
    return max(vals) if vals else 0.0


def _u(run: ReplayRun) -> float:
    ep = run.episode
    return ep.n_revealed / ep.N_max if ep.N_max > 0 else 0.0


@dataclass
class PolicyEvaluation:
    """Result of :func:`evaluate_policy` for one policy version."""

    label: str
    mode: Mode
    baked_beta: float | None
    V: float
    V_by_world: dict[str, float]
    eq1: dict[str, dict[str, Any]]
    default_runs: list[ReplayRun]
    sweep_runs: list[ReplayRun] = field(default_factory=list)
    sweep: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        """No episode raised, timed out or broke the sandbox."""
        return not self.errors

    @property
    def pareto_reward(self) -> float | None:
        return self.sweep.get("pareto.reward")

    @property
    def pareto_auc(self) -> float | None:
        return self.sweep.get("pareto.auc")

    @property
    def parallel_penalty(self) -> float | None:
        return self.sweep.get("parallel_penalty")

    def sweep_points(self) -> tuple[tuple[float, float, float, float], ...]:
        """(β, attainment, work, reward) per swept β — ``rsi_math.BetaSweep`` points."""
        return tuple((p["beta"], p["attainment"], p["work"], p["reward"])
                     for p in self.sweep.get("frontier", ()))

    def summary(self) -> dict[str, Any]:
        return {"label": self.label, "mode": self.mode, "baked_beta": self.baked_beta,
                "V": self.V, "V_by_world": dict(self.V_by_world), "eq1": self.eq1,
                "pareto.reward": self.pareto_reward, "pareto.auc": self.pareto_auc,
                "parallel_penalty": self.parallel_penalty, "eligible": self.eligible,
                "errors": list(self.errors)}

    def write(self, out_dir: str | os.PathLike[str]) -> None:
        """Write ``eval.json`` and ``proposal_results/{beta_sweep.json,policy_execution_traces.jsonl}``."""
        root = Path(out_dir)
        pr = root / "proposal_results"
        pr.mkdir(parents=True, exist_ok=True)
        _atomic(pr / "beta_sweep.json", json.dumps(_clean(self.sweep), indent=1,
                                                   default=_json_default))
        lines = []
        for run in self.default_runs:
            lines.append(json.dumps(run.to_trace_json(role="default", V=self.V_by_world.get(
                run.episode.world_id)), default=_json_default))
        for run in self.sweep_runs:
            lines.append(json.dumps(run.to_trace_json(role="sweep"), default=_json_default))
        _atomic(pr / "policy_execution_traces.jsonl", "\n".join(lines) + ("\n" if lines else ""))
        _atomic(root / "eval.json", json.dumps(_clean(self.summary()), indent=1,
                                               default=_json_default))


def _clean(o: Any) -> Any:
    """Replace non-finite floats by None (strict JSON)."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, Mapping):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _json_default(o: Any) -> Any:
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def evaluate_policy(policy: Any, worlds: Sequence[World], cfg: EvalConfig | None = None, *,
                    label: str = "", history: Sequence[LiveCycleManifest] = (),
                    sweep: bool = True, out_dir: str | os.PathLike[str] | None = None
                    ) -> PolicyEvaluation:
    """Evaluate one policy version on ``worlds`` (Eq.1 at the baked β + optional β sweep)."""
    cfg = cfg or EvalConfig()
    if not worlds:
        raise ValueError("evaluation needs at least one world")

    def run(world: World, beta: float | None) -> ReplayRun:
        return run_episode(policy, world, beta, cfg.W_for(world),
                           root_policy=cfg.root_policy or "choose",
                           root_multi=bool(cfg.root_multi), K2=cfg.K2, timeout=cfg.timeout,
                           apply_plan=bool(cfg.apply_plan),
                           hard_max_branch_count=cfg.hard_max_branch_count,
                           hard_max_refine_count=cfg.hard_max_refine_count, history=history)

    errors: list[str] = []
    default_runs: list[ReplayRun] = []
    V_by_world: dict[str, float] = {}
    eq1: dict[str, dict[str, Any]] = {}
    baked: float | None = None
    for w in worlds:
        r = run(w, None)
        default_runs.append(r)
        if r.error:
            errors.append(f"{w.world_id} (default beta): {r.error}")
        if baked is None and r.beta is not None:
            baked = r.beta
        W = cfg.W_for(w)
        bc, bp = cfg.betas_eq1(W)
        norm = w.normalizer(cfg.mode)
        v = replay_value(r.episode, beta_cost=bc, beta_par=bp, norm=norm)
        V_by_world[w.world_id] = v
        ep = r.episode
        vals = [norm(s) for s in ep.revealed_scores]
        if ep.root_score is not None:
            vals.append(norm(ep.root_score))
        eq1[w.world_id] = {"best": max(vals) if vals else 0.0, "N": ep.n_revealed,
                           "rounds": ep.rounds, "cost": bc * ep.n_revealed,
                           "parallel_bonus": bp * ep.n_revealed / max(1, ep.rounds),
                           "beta_cost": bc, "beta_par": bp, "W": W,
                           "stop_reason": ep.stop_reason}
    V = mean_value([V_by_world[w.world_id] for w in worlds])
    result = PolicyEvaluation(label=label, mode=cfg.mode, baked_beta=baked, V=V,
                              V_by_world=V_by_world, eq1=eq1, default_runs=default_runs,
                              errors=errors)
    if sweep:
        _sweep(result, worlds, cfg, run)
    if out_dir is not None:
        result.write(out_dir)
    return result


def _sweep(result: PolicyEvaluation, worlds: Sequence[World], cfg: EvalConfig,
           run: Any) -> None:
    by_world = {w.world_id: w for w in worlds}
    runs: list[ReplayRun] = []
    points: dict[str, list[tuple[float, float]]] = {w.world_id: [] for w in worlds}
    per_world: dict[str, list[dict[str, Any]]] = {w.world_id: [] for w in worlds}
    frontier = []
    for beta in cfg.beta_grid:
        at_beta: list[ReplayRun] = []
        single: dict[str, list[tuple[float, float]]] = {}
        for w in worlds:
            r = run(w, beta)
            # the sweep fixes β: report the grid value even if the policy ignores it
            r = replace(r, beta=float(beta))
            if r.error:
                result.errors.append(f"{w.world_id} (beta {beta}): {r.error}")
            u, q = _u(r), _q(r, w)
            points[w.world_id].append((u, q))
            single[w.world_id] = [(u, q)]
            ep = r.episode
            per_world[w.world_id].append({
                "beta": beta, "u": u, "q": q, "probes": ep.n_revealed, "rounds": ep.rounds,
                "penalty": episode_penalty(ep), "stop_reason": ep.stop_reason,
                "in_support": None if r.validity is None else r.validity.rewardable,
                "anytime_auc": anytime_auc(ep.curve, ep.N_max)})
            at_beta.append(r)
        runs += at_beta
        pen = parallel_penalty([r.episode for r in at_beta])
        auc_b = pareto_auc_v1(single)
        frontier.append({
            "beta": beta,
            "attainment": mean_value([p[0][1] for p in single.values()]),
            "work": mean_value([p[0][0] for p in single.values()]),
            "reward": pareto_reward(auc_b, pen, cfg.lam),
            "auc": auc_b, "parallel_penalty": pen,
            "probes": mean_value([float(r.episode.n_revealed) for r in at_beta]),
            "rounds": mean_value([float(r.episode.rounds) for r in at_beta]),
            "anytime_auc": mean_value([anytime_auc(r.episode.curve, r.episode.N_max)
                                       for r in at_beta])})
    auc = pareto_auc_v1(points)
    pen = parallel_penalty([r.episode for r in runs])
    result.sweep_runs = runs
    result.sweep = {
        "pareto_auc_version": PARETO_AUC_VERSION,
        "pareto.reward": pareto_reward(auc, pen, cfg.lam),
        "pareto.auc": auc,
        "parallel_penalty": pen,
        "lambda": cfg.lam,
        "beta_grid": list(cfg.beta_grid),
        "baked_beta": result.baked_beta,
        "V": result.V,
        "V_by_world": dict(result.V_by_world),
        "anytime_auc": mean_value([anytime_auc(r.episode.curve, r.episode.N_max) for r in runs]),
        "frontier": frontier,
        "per_world": per_world,
        "worlds": {wid: {"N_max": by_world[wid].N_max,
                         "trace_branch_count": by_world[wid].trace_branch_count,
                         "trace_refine_count": by_world[wid].trace_refine_count}
                   for wid in by_world},
        "config": cfg.to_json(),
    }


def load_sweep(version_dir: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Read ``proposal_results/beta_sweep.json`` of a version directory (None if absent)."""
    p = Path(version_dir) / "proposal_results" / "beta_sweep.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def sweep_points_from_json(sweep: Mapping[str, Any]) -> tuple[tuple[float, float, float, float], ...]:
    """``(β, attainment, work, reward)`` points of an archived ``beta_sweep.json``."""
    return tuple((float(p["beta"]), float(p["attainment"]), float(p["work"]), float(p["reward"]))
                 for p in sweep.get("frontier") or ())


__all__ = ["DEFAULT_BETA_GRID", "EvalConfig", "PARETO_AUC_VERSION", "PolicyEvaluation",
           "evaluate_policy", "load_sweep", "sweep_points_from_json"]
