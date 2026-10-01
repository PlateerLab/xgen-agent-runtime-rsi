"""실험 1 — 같은 정책·같은 과제·같은 검증기로 geny(기존 21-stage)와 geny-rsi(H0)를 잰다.

    python experiments/compare_engines.py --policy sonnet5=/path/policy.json --policy gpt6sol=/path/policy.json \
        --suite runs/xgen-core --out runs/e1 --k 2 --splits evolve heldout --parallel 4

결과: ``OUT/<label>/<engine>/<split>/eval.json`` + 시행별 outcome.json, 요약 ``OUT/summary.json``.
정책 JSON(키 포함)은 저장소 밖에 둔다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from xgen_rsi.evolve.runner import PolicySpec, evaluate
from xgen_rsi.evolve.tasks import load_suite
from xgen_rsi.kernel.executor import BUILTIN_H0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", action="append", required=True, help="LABEL=policy.json")
    ap.add_argument("--suite", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--splits", nargs="+", default=["evolve", "heldout"])
    ap.add_argument("--engines", nargs="+", default=["geny", "geny-rsi"])
    ap.add_argument("--harness", default=str(BUILTIN_H0))
    ap.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()
    suite = load_suite(args.suite)
    summary = {}
    for item in args.policy:
        label, path = item.split("=", 1)
        policy = PolicySpec.from_json(json.loads(Path(path).read_text(encoding="utf-8")))
        for split in args.splits:
            for engine in args.engines:
                out = Path(args.out) / label / engine / split
                ev = evaluate(args.harness, suite.split(split), args.k, policy=policy, out_dir=str(out),
                              job=f"{label}-{engine}-{split}", parallel=args.parallel, engine=engine)
                row = {"S": ev.S, "C": ev.C, "missing": ev.missing, "n": ev.n_expected, **ev.extra}
                summary[f"{label}/{engine}/{split}"] = row
                print(json.dumps({"run": f"{label}/{engine}/{split}", **row}, ensure_ascii=False), flush=True)
                Path(args.out, "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
