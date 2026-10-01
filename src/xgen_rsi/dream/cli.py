"""``rsi dream`` — Dream-RSI 탐색 정책 진화(L2) 명령.

    rsi dream explore POOL --suite DIR --policy policy.json --explorer builtin:portfolio --iteration 0
                     [--split evolve] [--harness DIR] [--plan auto|fixed] [--branches 3] [--refine 2] [--W 3]
                     [--beta B] [--feedback checks|score|none] [--limit N] [--parallel-tasks 1]
        검증기가 있는 과제를 라이브로 탐색한다. 결과: POOL/iter####/<task>/{episode,tree,live_cycle_manifest}.json
    rsi dream build-worlds OUT.json [--trace-pool POOL] [--eval-job NAME=DIR ...] [--suite DIR]
        탐색 트리와 평가 시행을 재생 world 풀로 묶는다
    rsi dream cycle CYCLE_DIR --worlds OUT.json --incumbent builtin:portfolio|policy.py --iteration N
                   [--llm role.json] [--M 4] [--mode xgen|paper] [--trace-pool POOL] [--prior-cycle DIR ...]
                   [--confirm-suite DIR --policy policy.json [--confirm-split evolve] [--harness DIR]
                    --delta D [--branches 3] [--refine 2] [--W 3] [--confirm-limit N]]
        π^0..π^{M−1} → 재생 평가 → argmax V → (온라인 확인 = RRSI 판정) → promoted_policy.py
    rsi dream status CYCLE_DIR

탐색 정책은 소스로 다룬다: ``builtin:parallel_refine`` · ``builtin:portfolio`` 또는 파일 경로. 모든 정책 코드는
샌드박스(:mod:`xgen_rsi.dream.sandbox`)를 거쳐 실행된다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_MAX_BRANCHES = 8
DEFAULT_MAX_REFINE = 6


def _read_json(path: str | Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def policy_source(ref: str) -> str:
    """``builtin:<name>`` 또는 파일 경로 → 정책 소스."""
    from xgen_rsi.explore.policies import builtin_policy_source

    if ref.startswith("builtin:"):
        return builtin_policy_source(ref.split(":", 1)[1])
    return Path(ref).read_text(encoding="utf-8")


def _tasks(suite_dir: str, split: str, limit: int) -> List[Any]:
    from xgen_rsi.evolve.tasks import load_suite

    tasks = load_suite(suite_dir).split(split)
    if not tasks:
        raise SystemExit(f"split {split!r} of {suite_dir} has no tasks")
    return tasks[:limit] if limit else tasks


def plan_for(loaded: Any, *, mode: str, B: int, R: int, W: int, history: Sequence[Any],
             max_branches: int = DEFAULT_MAX_BRANCHES, max_refine: int = DEFAULT_MAX_REFINE,
             config: Optional[Dict[str, Any]] = None) -> Any:
    """``auto``: 정책의 ``plan_grid``(라이브 이력과 함께), 실패하면 고정 격자. ``fixed``: 고정 격자."""
    from xgen_rsi.dream.develop import run_plan_grid
    from xgen_rsi.explore.api import GridPlan, GridPlanningContext

    fixed = GridPlan(branch_count=B, refine_count=R, reason="fixed grid from the command line")
    if mode == "fixed":
        return fixed
    ctx = GridPlanningContext(hard_max_branch_count=max(max_branches, B), hard_max_refine_count=max(max_refine, R),
                              worker_cap=W, fallback_branch_count=B, fallback_refine_count=R,
                              history=tuple(history))
    plan = run_plan_grid(loaded, ctx, config)
    if plan is None:
        return GridPlan(branch_count=B, refine_count=R, reason="plan_grid failed; fixed grid")
    return GridPlan(branch_count=max(1, min(plan.branch_count, ctx.hard_max_branch_count)),
                    refine_count=max(0, min(plan.refine_count, ctx.hard_max_refine_count)), reason=plan.reason)


def run_live(source: str, tasks: Sequence[Any], *, out_dir: str, policy: Any, harness: str, iteration: int,
             plan_mode: str, B: int, R: int, W: int, beta: Optional[float], history: Sequence[Any],
             feedback: str = "checks", parallel_tasks: int = 1, client_factory: Any = None,
             policy_version: str = "") -> Dict[str, Any]:
    """정책 소스 하나로 과제들을 라이브 탐색한다(탐색·온라인 확인이 함께 쓴다)."""
    import hashlib

    from xgen_rsi.discovery import episodes_eval, explore_suite
    from xgen_rsi.dream.sandbox import load_policy

    loaded = load_policy(source, require=("solve",))
    config: Dict[str, Any] = {} if beta is None else {"beta": float(beta)}
    baked = float(loaded(config).beta)
    plan = plan_for(loaded, mode=plan_mode, B=B, R=R, W=W, history=history, config=config)
    version = policy_version or "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    episodes = explore_suite(tasks, lambda: loaded(config), plan=plan, W=W, harness_dir=harness, policy=policy,
                             out_dir=out_dir, iteration=iteration, beta=baked, policy_version=version,
                             client_factory=client_factory, feedback=feedback, parallel_tasks=parallel_tasks)
    ev = episodes_eval(episodes, job=Path(out_dir).name)
    summary = {"iteration": iteration, "policy_version": version, "beta": baked,
               "plan": {"branch_count": plan.branch_count, "refine_count": plan.refine_count, "reason": plan.reason},
               "W": W, "S": ev.S, "C": ev.C, "missing": ev.missing, "n_tasks": len(episodes),
               "episodes": [{"task_id": e.task_id, "best": e.best, "baseline": e.baseline, "probes": e.probes,
                             "policy_tokens": e.policy_tokens} for e in episodes]}
    Path(out_dir, "explore_summary.json").write_text(json.dumps(summary, ensure_ascii=True, indent=1),
                                                     encoding="utf-8")
    return {"summary": summary, "eval": ev}


# ── commands ────────────────────────────────────────────────────────────


def cmd_explore(args: argparse.Namespace) -> int:
    from xgen_rsi.dream.manifest import load_live_manifests
    from xgen_rsi.evolve.runner import PolicySpec
    from xgen_rsi.kernel.executor import BUILTIN_H0

    pool = Path(args.pool)
    out = run_live(policy_source(args.explorer), _tasks(args.suite, args.split, args.limit),
                   out_dir=str(pool / f"iter{args.iteration:04d}"), policy=PolicySpec.from_json(_read_json(args.policy)),
                   harness=args.harness or str(BUILTIN_H0), iteration=args.iteration, plan_mode=args.plan,
                   B=args.branches, R=args.refine, W=args.W, beta=args.beta,
                   history=load_live_manifests(pool), feedback=args.feedback, parallel_tasks=args.parallel_tasks)
    print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
    return 0


def cmd_build_worlds(args: argparse.Namespace) -> int:
    from xgen_rsi.discovery import Episode, baseline_score, worlds_from_episodes
    from xgen_rsi.dream.world import World, WorldPool
    from xgen_rsi.evolve.runner import load_outcomes
    from xgen_rsi.evolve.tasks import load_suite

    worlds: List[World] = []
    if args.trace_pool:
        paths = sorted(Path(args.trace_pool).glob("iter*/*/episode.json"))
        eps = [Episode.from_json(_read_json(p)) for p in paths]
        worlds += worlds_from_episodes(eps)
    suite = load_suite(args.suite) if args.suite else None
    for item in args.eval_job or ():
        if "=" not in item:
            raise SystemExit(f"--eval-job expects NAME=DIR, got {item!r}")
        name, job_dir = item.split("=", 1)
        outs = load_outcomes(job_dir)
        for task_id in sorted({o.task_id for o in outs}):
            base = baseline_score(suite.tasks[task_id]) if suite is not None and task_id in suite.tasks else 0.0
            worlds.append(World.from_trial_outcomes(task_id, outs, harness_id=name, baseline_score=base))
    if not worlds:
        raise SystemExit("no worlds found (give --trace-pool and/or --eval-job)")
    pool = WorldPool(worlds)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pool.save(args.out)
    print(json.dumps({"worlds": len(pool), "ids": list(pool.ids)[:20]}, ensure_ascii=False, indent=1))
    return 0


def _prior_sweeps(dirs: Sequence[str]) -> List[Any]:
    from xgen_rsi.rsi_math.types import BetaSweep

    out = []
    for d in dirs:
        m = _read_json(Path(d) / "cycle_manifest.json")
        if m.get("complete"):
            out.append(BetaSweep(iteration=int(m["iteration"]), points=[tuple(p) for p in m.get("pi0_sweep_points") or ()]))
    return out


def make_confirmation(args: argparse.Namespace, cycle_dir: Path) -> Any:
    """온라인 확인 훅 — 후보와 현재 π_E 로 같은 과제를 라이브 탐색해 RRSI 판정(``rrsi_confirmation``)."""
    from xgen_rsi.discovery import rrsi_confirmation
    from xgen_rsi.dream.cycle import ConfirmationResult
    from xgen_rsi.dream.manifest import load_live_manifests
    from xgen_rsi.evolve.runner import PolicySpec
    from xgen_rsi.kernel.executor import BUILTIN_H0

    if args.delta is None:
        raise SystemExit("--confirm-suite needs --delta (the noise band of the live measurement)")
    if not args.policy:
        raise SystemExit("--confirm-suite needs --policy")
    tasks = _tasks(args.confirm_suite, args.confirm_split, args.confirm_limit)
    policy = PolicySpec.from_json(_read_json(args.policy))
    harness = args.harness or str(BUILTIN_H0)
    history = load_live_manifests(args.trace_pool) if args.trace_pool else []

    def confirm(req: Any) -> ConfirmationResult:
        runs = {}
        for side, source, beta in (("incumbent", args_incumbent_source(args), req.incumbent_beta),
                                   ("candidate", req.candidate_source, req.candidate_beta)):
            runs[side] = run_live(source, tasks, out_dir=str(cycle_dir / "confirm" / side), policy=policy,
                                  harness=harness, iteration=req.iteration, plan_mode=args.plan,
                                  B=args.branches, R=args.refine, W=args.W, beta=beta, history=history)
        verdict = rrsi_confirmation(runs["candidate"]["eval"], runs["incumbent"]["eval"], delta=float(args.delta))
        details = dict(verdict["details"])
        details["incumbent"] = runs["incumbent"]["summary"]
        details["candidate"] = runs["candidate"]["summary"]
        return ConfirmationResult(approved=verdict["approved"], reason=verdict["reason"], details=details)

    return confirm


def args_incumbent_source(args: argparse.Namespace) -> str:
    return policy_source(args.incumbent)


def cmd_cycle(args: argparse.Namespace) -> int:
    from xgen_rsi.dream.cycle import CycleConfig, DreamCycle
    from xgen_rsi.dream.manifest import load_live_manifests
    from xgen_rsi.dream.world import WorldPool

    cycle_dir = Path(args.cycle_dir)
    llm = None
    if args.llm:
        from xgen_rsi.roles.llm import RoleLLM, RoleModel

        llm = RoleLLM(RoleModel.from_json(_read_json(args.llm)), role="policy_dev")
    confirm = make_confirmation(args, cycle_dir) if args.confirm_suite else None
    cycle = DreamCycle(
        cycle_dir,
        WorldPool.load(args.worlds),
        args_incumbent_source(args),
        llm=llm,
        iteration=args.iteration,
        config=CycleConfig(M=args.M, mode=args.mode),
        online_confirm=confirm,
        live_manifests=load_live_manifests(args.trace_pool) if args.trace_pool else (),
        prior_sweeps=_prior_sweeps(args.prior_cycle or ()),
    )
    result = cycle.run()
    print(json.dumps({k: v for k, v in result.to_json().items() if k != "pi0_sweep_points"},
                     ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    path = Path(args.cycle_dir) / "cycle_manifest.json"
    if not path.exists():
        print(json.dumps({"cycle_dir": args.cycle_dir, "complete": False}))
        return 0
    m = _read_json(path)
    keys = ("iteration", "status", "m_star", "promoted_label", "V_selection", "eligible", "labels", "next_beta",
            "next_plan", "confirmation", "mode", "M")
    print(json.dumps({k: m.get(k) for k in keys}, ensure_ascii=False, indent=1, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rsi dream", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)

    def grid_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--plan", choices=("auto", "fixed"), default="auto")
        p.add_argument("--branches", type=int, default=3)
        p.add_argument("--refine", type=int, default=2)
        p.add_argument("--W", type=int, default=3)

    p = sub.add_parser("explore", help="라이브 탐색(검증기가 있는 과제)")
    p.add_argument("pool", help="trace pool 디렉터리(반복마다 iter#### 하위 디렉터리)")
    p.add_argument("--suite", required=True)
    p.add_argument("--split", default="evolve")
    p.add_argument("--policy", required=True, help="정책 π JSON")
    p.add_argument("--harness")
    p.add_argument("--explorer", default="builtin:portfolio")
    p.add_argument("--iteration", type=int, required=True)
    p.add_argument("--beta", type=float)
    p.add_argument("--feedback", choices=("checks", "score", "none"), default="checks")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--parallel-tasks", type=int, default=1)
    grid_args(p)

    p = sub.add_parser("build-worlds", help="탐색 트리·평가 시행 → world 풀")
    p.add_argument("out")
    p.add_argument("--trace-pool")
    p.add_argument("--eval-job", action="append", help="NAME=DIR (evolve 평가 job 디렉터리)")
    p.add_argument("--suite", help="시행 world 의 루트 점수를 과제별로 계산할 스위트")

    p = sub.add_parser("cycle", help="Dream 사이클 하나")
    p.add_argument("cycle_dir")
    p.add_argument("--worlds", required=True)
    p.add_argument("--incumbent", required=True, help="현재 π_E: builtin:<name> 또는 policy.py")
    p.add_argument("--iteration", type=int, required=True)
    p.add_argument("--llm", help="정책 개발 역할 모델 JSON {provider, model, api_key|credentials}")
    p.add_argument("--M", type=int, default=4)
    p.add_argument("--mode", choices=("xgen", "paper"), default="xgen")
    p.add_argument("--trace-pool")
    p.add_argument("--prior-cycle", action="append")
    p.add_argument("--confirm-suite")
    p.add_argument("--confirm-split", default="evolve")
    p.add_argument("--confirm-limit", type=int, default=0)
    p.add_argument("--policy")
    p.add_argument("--harness")
    p.add_argument("--delta", type=float)
    grid_args(p)

    p = sub.add_parser("status")
    p.add_argument("cycle_dir")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return {"explore": cmd_explore, "build-worlds": cmd_build_worlds, "cycle": cmd_cycle,
            "status": cmd_status}[args.action](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
