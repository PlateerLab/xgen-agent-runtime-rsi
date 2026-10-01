"""``rsi`` 명령 — 하네스 검사, 스위트, 평가, RRSI 진화(L1), Dream-RSI 사이클(L2).

정책·역할 모델의 자격증명은 JSON 파일로 넘긴다(XGEN 에 등록된 LLM 의 provider·model·키). 환경변수에서 키를 읽지 않는다.

    rsi harness show [DIR]                      하네스 구성요소·버전·편집 주소
    rsi harness validate [DIR]
    rsi harness diff A B                        두 버전의 편집 주소 차이와 kind(=RRSI 태그)
    rsi suite build OUT [--suite xgen-core|xgen-hard|xgen-pro] [--per-category N]   내장 스위트 생성
    rsi eval --harness DIR --suite DIR --split evolve --k 2 --policy policy.json --out OUT [--engine geny-rsi|geny]
    rsi evolve {baseline,calibrate,round,run,readjudicate,reevaluate,heldout,status} ...   (RRSI L1)
    rsi dream {build-worlds,cycle,status} ...   (Dream-RSI L2)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_json(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── harness ─────────────────────────────────────────────────────────────


def cmd_harness(args: argparse.Namespace) -> int:
    from xgen_rsi.harness.runtime import instantiate
    from xgen_rsi.harness.spec import addresses_for, load_manifest, parse_address
    from xgen_rsi.kernel.executor import BUILTIN_H0

    if args.action in ("show", "validate"):
        root = Path(args.dir) if getattr(args, "dir", None) else BUILTIN_H0
        m = load_manifest(root)
        instantiate(m)  # 구현·프로토콜 검사까지
        if args.action == "validate":
            print(f"ok {m.name} {m.version_id()}")
            return 0
        print(json.dumps(
            {
                "name": m.name,
                "version": m.version_id(),
                "enabled_kinds": list(m.enabled_kinds),
                "components": [{"id": c.id, "kind": c.kind, "impl": c.impl, "files": list(c.files)} for c in m.components],
                "edit_addresses": list(addresses_for(m)),
                "locked": list(m.locked),
            },
            ensure_ascii=False,
            indent=1,
        ))
        return 0
    if args.action == "diff":
        a, b = load_manifest(args.a), load_manifest(args.b)
        addr_a = {x: _safe_get(a, x) for x in addresses_for(a)}
        addr_b = {x: _safe_get(b, x) for x in addresses_for(b)}
        changed = sorted(x for x in set(addr_a) | set(addr_b) if addr_a.get(x) != addr_b.get(x))
        kinds: List[str] = []
        for x in changed:
            comp_id = parse_address(x)[0]
            for m in (b, a):
                try:
                    k = m.kind_of(comp_id)
                except KeyError:
                    continue
                if k not in kinds:
                    kinds.append(k)
                break
        print(json.dumps({"from": a.version_id(), "to": b.version_id(), "changed": changed, "kinds": kinds}, ensure_ascii=False, indent=1))
        return 0
    return 2


def _safe_get(m: Any, address: str) -> Any:
    try:
        return m.get(address)
    except Exception:  # noqa: BLE001
        return None


# ── suite ───────────────────────────────────────────────────────────────


def cmd_suite(args: argparse.Namespace) -> int:
    from xgen_rsi.evolve.tasks import write_suite

    if args.suite == "xgen-hard":
        from xgen_rsi.suites.build_xgen_hard import SUITE_NAME, build
    elif args.suite == "xgen-pro":
        from xgen_rsi.suites.build_xgen_pro import SUITE_NAME, build
    else:
        from xgen_rsi.suites.build_xgen_core import SUITE_NAME, build

    tasks, splits = build(per_category=args.per_category)
    write_suite(SUITE_NAME, tasks, splits, args.out)
    print(f"wrote {len(tasks)} tasks to {args.out}: " + ", ".join(f"{k}={len(v)}" for k, v in splits.items()))
    return 0


# ── eval ────────────────────────────────────────────────────────────────


def cmd_eval(args: argparse.Namespace) -> int:
    from xgen_rsi.evolve.runner import PolicySpec, evaluate
    from xgen_rsi.evolve.tasks import load_suite

    suite = load_suite(args.suite)
    tasks = suite.split(args.split)
    if args.limit:
        tasks = tasks[: args.limit]
    policy = PolicySpec.from_json(_load_json(args.policy))
    ev = evaluate(args.harness, tasks, args.k, policy=policy, out_dir=args.out, job=args.job or args.split,
                  parallel=args.parallel, engine=args.engine)
    print(json.dumps({"S": ev.S, "C": ev.C, "n_expected": ev.n_expected, "missing": ev.missing, "extra": ev.extra}, ensure_ascii=False, indent=1))
    return 0


# ── evolve / dream (모듈이 있으면 위임) ──────────────────────────────────


def cmd_evolve(args: argparse.Namespace, rest: List[str]) -> int:
    try:
        from xgen_rsi.evolve import cli as evolve_cli  # type: ignore[attr-defined]
    except ImportError:
        print("evolve CLI is not available in this build", file=sys.stderr)
        return 2
    return int(evolve_cli.main(rest) or 0)


def cmd_dream(args: argparse.Namespace, rest: List[str]) -> int:
    try:
        from xgen_rsi.dream import cli as dream_cli  # type: ignore[attr-defined]
    except ImportError:
        print("dream CLI is not available in this build", file=sys.stderr)
        return 2
    return int(dream_cli.main(rest) or 0)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rsi", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harness", help="하네스 검사")
    hs = h.add_subparsers(dest="action", required=True)
    p = hs.add_parser("show")
    p.add_argument("dir", nargs="?")
    p = hs.add_parser("validate")
    p.add_argument("dir", nargs="?")
    p = hs.add_parser("diff")
    p.add_argument("a")
    p.add_argument("b")

    s = sub.add_parser("suite", help="내장 스위트 생성")
    ss = s.add_subparsers(dest="action", required=True)
    p = ss.add_parser("build")
    p.add_argument("out")
    p.add_argument("--per-category", type=int, default=4)
    p.add_argument("--suite", choices=("xgen-core", "xgen-hard", "xgen-pro"), default="xgen-core")

    e = sub.add_parser("eval", help="하네스 하나를 스위트 분할에서 k 회 평가")
    e.add_argument("--harness", required=True)
    e.add_argument("--suite", required=True)
    e.add_argument("--split", default="evolve")
    e.add_argument("--k", type=int, default=2)
    e.add_argument("--policy", required=True, help="정책 JSON {provider, model, api_key|credentials, base_url?}")
    e.add_argument("--out", required=True)
    e.add_argument("--job", default="")
    e.add_argument("--parallel", type=int, default=4)
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--engine", choices=("geny-rsi", "geny"), default="geny-rsi",
                   help="geny-rsi = 이 패키지의 엔진(하네스 --harness), geny = 기존 21-stage 엔진(비교용)")

    sub.add_parser("evolve", help="RRSI 하네스 진화(L1) — 하위 명령은 rsi evolve -h", add_help=False)
    sub.add_parser("dream", help="Dream-RSI 탐색 정책 사이클(L2) — 하위 명령은 rsi dream -h", add_help=False)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("evolve", "dream"):
        ns = argparse.Namespace(cmd=argv[0])
        return cmd_evolve(ns, argv[1:]) if argv[0] == "evolve" else cmd_dream(ns, argv[1:])
    args = build_parser().parse_args(argv)
    if args.cmd == "harness":
        return cmd_harness(args)
    if args.cmd == "suite":
        return cmd_suite(args)
    if args.cmd == "eval":
        return cmd_eval(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
