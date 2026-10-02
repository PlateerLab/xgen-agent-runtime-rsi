"""``rsi evolve`` — RRSI 하네스 진화(L1) 명령.

진화 실행 하나 = 디렉터리 하나(``RUN``). ``init`` 이 ``RUN/run.json`` 에 스위트·정책·설정 파일의 *경로*를 적고,
나머지 명령은 그것을 읽는다. 자격증명은 정책 JSON·``rrsi.json`` 의 역할 블록에만 있다(``run.json`` 과 frontier 에는
쓰지 않는다).

    rsi evolve init RUN --suite DIR --policy policy.json --config rrsi.json [--harness DIR] [--name NAME]
    rsi evolve baseline RUN [--job base]
    rsi evolve calibrate RUN [--jobs base base_r2]
    rsi evolve round RUN T [--dry-run]
    rsi evolve run RUN [--T N] [--start 0]           baseline·calibrate 가 없으면 먼저, 그다음 라운드들(STOP 파일로 멈춤)
    rsi evolve readjudicate RUN T                    저장된 측정으로 Algorithm 2 를 다시(평가 0 회)
    rsi evolve reevaluate RUN T [--variants A B]
    rsi evolve heldout RUN LABEL [--split heldout] [--ref COMMIT]
    rsi evolve status RUN
    rsi evolve export RUN OUT [--ref COMMIT]         채택된 하네스(기본: 현재 incumbent)를 디렉터리로 — 배포용
                                                     (XGEN_RSI_HARNESS_DIR=OUT 또는 GenyRSI(harness=OUT))
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RUN_FILE = "run.json"


def _read_json(path: str | Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return data


def cmd_init(args: argparse.Namespace) -> int:
    from xgen_rsi.evolve.tasks import load_suite
    from xgen_rsi.harness.spec import load_manifest
    from xgen_rsi.kernel.executor import BUILTIN_H0

    run = Path(args.run)
    run.mkdir(parents=True, exist_ok=True)
    suite = load_suite(args.suite)  # 형식 검사
    harness = Path(args.harness).resolve() if args.harness else BUILTIN_H0
    load_manifest(harness)
    _read_json(args.policy)
    _read_json(args.config)
    spec = {
        "name": args.name or f"{suite.name}",
        "suite": str(Path(args.suite).resolve()),
        "policy": str(Path(args.policy).resolve()),
        "config": str(Path(args.config).resolve()),
        "harness": str(harness),
    }
    (run / RUN_FILE).write_text(json.dumps(spec, ensure_ascii=True, indent=1), encoding="utf-8")
    print(json.dumps(spec, indent=1))
    return 0


def open_run(run_dir: str, **cfg_overrides: Any) -> Any:
    """``RUN/run.json`` → :class:`~xgen_rsi.evolve.round.EvolveRun`."""
    from xgen_rsi.evolve.config import EvolveConfig
    from xgen_rsi.evolve.domain import EvolveDomain
    from xgen_rsi.evolve.round import EvolveRun
    from xgen_rsi.evolve.runner import PolicySpec
    from xgen_rsi.evolve.tasks import load_suite

    path = Path(run_dir) / RUN_FILE
    if not path.exists():
        raise SystemExit(f"{path} not found — run `rsi evolve init` first")
    spec = _read_json(path)
    cfg = EvolveConfig.load(spec["config"], **cfg_overrides)
    domain = EvolveDomain(
        name=spec["name"],
        suite=load_suite(spec["suite"]),
        policy=PolicySpec.from_json(_read_json(spec["policy"])),
        max_valid_rate_drop=cfg.max_valid_rate_drop,
        max_nosub_rise=cfg.max_nosub_rise,
    )
    if cfg.judge is not None:
        from xgen_rsi.evolve.judge import CriteriaJudge

        domain.judge = CriteriaJudge(cfg.judge)
    return EvolveRun(domain, cfg, run_dir, start_harness=spec["harness"], name=spec["name"])


def _print(obj: Any) -> None:
    if hasattr(obj, "to_json"):
        obj = obj.to_json()
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def _ev_summary(ev: Any) -> Dict[str, Any]:
    return {"job": ev.job, "S": ev.S, "C": ev.C, "n_expected": ev.n_expected, "missing": ev.missing,
            "extra": ev.extra}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rsi evolve", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("init", help="진화 실행 디렉터리를 만든다")
    p.add_argument("run")
    p.add_argument("--suite", required=True)
    p.add_argument("--policy", required=True, help="정책 π JSON {provider, model, api_key|credentials, base_url?}")
    p.add_argument("--config", required=True, help="rrsi.json (RRSIParams + 엔지니어링 값 + roles)")
    p.add_argument("--harness", help="시작 하네스 디렉터리(기본: 내장 H0)")
    p.add_argument("--name")
    p = sub.add_parser("baseline", help="H_0 평가 → frontier 시작")
    p.add_argument("run")
    p.add_argument("--job", default="base")
    p = sub.add_parser("calibrate", help="δ 보정(R ≥ 2 반복 평가 권장)")
    p.add_argument("run")
    p.add_argument("--jobs", nargs="+", default=["base", "base_r2"])
    p = sub.add_parser("round", help="라운드 t 하나")
    p.add_argument("run")
    p.add_argument("t", type=int)
    p.add_argument("--dry-run", action="store_true", help="분석까지만(제안 전 멈춤)")
    p = sub.add_parser("run", help="드라이버: baseline·calibrate 후 라운드 start..T-1")
    p.add_argument("run")
    p.add_argument("--T", type=int)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--calibration-jobs", nargs="+", default=["base", "base_r2"])
    p = sub.add_parser("readjudicate", help="저장된 측정으로 라운드 t 재판정")
    p.add_argument("run")
    p.add_argument("t", type=int)
    p = sub.add_parser("reevaluate", help="라운드 t 후보 재측정 후 재판정")
    p.add_argument("run")
    p.add_argument("t", type=int)
    p.add_argument("--variants", nargs="*")
    p = sub.add_parser("heldout", help="보류 분할 평가(보고 전용 — 어떤 판정에도 쓰지 않음)")
    p.add_argument("run")
    p.add_argument("label")
    p.add_argument("--split", default="heldout")
    p.add_argument("--ref")
    p.add_argument("--k", type=int)
    p = sub.add_parser("status", help="frontier 요약")
    p.add_argument("run")
    p = sub.add_parser("export", help="채택된 하네스를 디렉터리로 내보낸다(배포용)")
    p.add_argument("run")
    p.add_argument("out")
    p.add_argument("--ref", help="커밋(기본: 현재 incumbent)")
    return ap


def export_harness(run: Any, out: str, ref: Optional[str] = None) -> Dict[str, Any]:
    """incumbent(또는 ``ref``) 하네스를 ``out`` 으로 복사하고 버전을 검사한다."""
    import shutil

    from xgen_rsi.harness.runtime import instantiate
    from xgen_rsi.harness.spec import load_manifest

    target = ref or str(run.frontier()["incumbent"]["commit"])
    wt = run.repo.worktree_detached(run.wt_root / f"export_{target[:12]}", target)
    try:
        src = run.repo.harness_dir(wt)
        dst = Path(out)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git"))
    finally:
        run.repo.worktree_remove(wt)
    m = load_manifest(dst)
    instantiate(m)
    return {"out": str(dst), "commit": target, "name": m.name, "version": m.version_id()}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "init":
        return cmd_init(args)
    run = open_run(args.run)
    if args.action == "baseline":
        _print(_ev_summary(run.baseline(args.job)))
    elif args.action == "calibrate":
        _print(run.calibrate(args.jobs))
    elif args.action == "round":
        _print(run.round(args.t, dry_run=args.dry_run))
    elif args.action == "run":
        from xgen_rsi.evolve.driver import drive

        out = drive(run, T=args.T, start=args.start, calibration_jobs=args.calibration_jobs)
        _print(out)
        return 0 if out["status"] in ("done", "stopped") else 1
    elif args.action == "readjudicate":
        _print(run.readjudicate(args.t))
    elif args.action == "reevaluate":
        _print(run.reevaluate(args.t, args.variants or None))
    elif args.action == "heldout":
        _print(_ev_summary(run.heldout(args.label, args.split, ref=args.ref, k=args.k)))
    elif args.action == "status":
        _print(run.status())
    elif args.action == "export":
        _print(export_harness(run, args.out, args.ref))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
