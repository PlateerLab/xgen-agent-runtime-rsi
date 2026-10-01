"""실험 2 — 실제 모델 응답을 녹음해 두 엔진에 그대로 다시 먹이고, 모든 호출의 요청을 비교한다(결정적 동등성).

    python experiments/replay_equivalence.py --policy sonnet5=/path/policy.json --suite runs/xgen-core \
        --tasks synth-weekly-0 recovery-missing-file-0 --out runs/e2

1) geny(기존 엔진)로 과제를 한 번 실제로 돌리며 공급자 응답을 순서대로 녹음한다(생각 블록·서명 포함).
2) 같은 응답을 각본처럼 내는 클라이언트로 geny 와 geny-rsi 를 다시 돌린다(도구는 새 작업 공간에서 실제로 실행).
3) 두 엔진이 공급자에 보낸 요청(시스템·메시지·도구·모델 설정)을 호출마다 비교한다 — 시각·작업 공간 경로만 정규화.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from xgen_rsi.base.host import runner as runner_mod
from xgen_rsi.evolve.runner import PolicySpec, run_trial
from xgen_rsi.evolve.tasks import load_suite
from xgen_rsi.kernel.executor import BUILTIN_H0

_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?([+-]\d{2}:?\d{2}|Z)?")
_TMP = re.compile(r"(/[^\s\"']*?)/(rec|play)-[\w-]+/")


def _norm(x: Any) -> Any:
    s = json.dumps(x, ensure_ascii=False, default=str, sort_keys=True)
    s = _TS.sub("<TS>", s)
    s = re.sub(r"[^\"\s]*/(rec|play)-[A-Za-z0-9_]+/[^\"\s]*?workspace", "<WS>", s)
    s = re.sub(r"(요일|day)[^\"<]{0,40}<TS>", "<DAY><TS>", s)
    return json.loads(s)


class _Recorder:
    def __init__(self, inner: Any, sink: List[Any]) -> None:
        self._i, self._sink = inner, sink

    def __getattr__(self, n: str) -> Any:
        return getattr(self._i, n)

    async def create_message(self, **kw: Any) -> Any:
        r = await self._i.create_message(**kw)
        self._sink.append(copy.deepcopy(r))
        return r


class _Replay:
    def __init__(self, inner: Any, responses: List[Any], requests: List[Any]) -> None:
        self._i, self._resp, self._req = inner, responses, requests

    def __getattr__(self, n: str) -> Any:
        return getattr(self._i, n)

    async def create_message(self, **kw: Any) -> Any:
        self._req.append({k: kw.get(k) for k in ("system", "messages", "tools", "model_config")})
        idx = len(self._req) - 1
        if idx >= len(self._resp):
            raise RuntimeError("replay exhausted: the engine made more calls than recorded")
        return copy.deepcopy(self._resp[idx])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True, help="LABEL=policy.json")
    ap.add_argument("--suite", required=True)
    ap.add_argument("--tasks", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    label, path = args.policy.split("=", 1)
    policy = PolicySpec.from_json(json.loads(Path(path).read_text(encoding="utf-8")))
    suite = load_suite(args.suite)
    orig = runner_mod.build_client
    report: Dict[str, Any] = {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for tid in args.tasks:
        task = suite.tasks[tid]
        recorded: List[Any] = []
        runner_mod.build_client = lambda *a, **k: _Recorder(orig(*a, **k), recorded)
        live = run_trial(task, 0, harness_dir=str(BUILTIN_H0), policy=policy,
                         out_dir=tempfile.mkdtemp(prefix="rec-", dir=out), engine="geny")
        reqs: Dict[str, List[Any]] = {}
        outs: Dict[str, Any] = {}
        for engine in ("geny", "geny-rsi"):
            reqs[engine] = []
            runner_mod.build_client = lambda *a, _e=engine, **k: _Replay(orig(*a, **k), recorded, reqs[_e])
            outs[engine] = run_trial(task, 0, harness_dir=str(BUILTIN_H0), policy=policy,
                                     out_dir=tempfile.mkdtemp(prefix="play-", dir=out), engine=engine)
        runner_mod.build_client = orig
        a, b = [_norm(x) for x in reqs["geny"]], [_norm(x) for x in reqs["geny-rsi"]]
        diffs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        row = {"model_calls_recorded": len(recorded), "calls_geny": len(a), "calls_geny_rsi": len(b),
               "identical_calls": sum(1 for x, y in zip(a, b) if x == y), "differing_calls": diffs,
               "reward_live": live.reward, "reward_geny": outs["geny"].reward, "reward_geny_rsi": outs["geny-rsi"].reward,
               "answer_equal": outs["geny"].answer == outs["geny-rsi"].answer,
               "usage_geny": outs["geny"].usage_tokens, "usage_geny_rsi": outs["geny-rsi"].usage_tokens}
        if diffs:
            i = diffs[0]
            for key in ("system", "messages", "tools", "model_config"):
                if a[i].get(key) != b[i].get(key):
                    row["first_diff"] = {"call": i, "field": key,
                                         "geny": json.dumps(a[i].get(key), ensure_ascii=False)[:3000],
                                         "geny_rsi": json.dumps(b[i].get(key), ensure_ascii=False)[:3000]}
                    break
        report[f"{label}/{tid}"] = row
        print(json.dumps({"task": f"{label}/{tid}", **{k: v for k, v in row.items() if k != "first_diff"}},
                         ensure_ascii=False), flush=True)
        if "first_diff" in row:
            print("  first diff:", row["first_diff"]["call"], row["first_diff"]["field"], flush=True)
    (out / f"replay_{label}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
