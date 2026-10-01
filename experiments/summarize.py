"""실험 결과 요약 — runs/ 의 시행 결과를 읽어 보고서용 표(JSON)를 만든다(대화 내용·답은 싣지 않는다).

    python experiments/summarize.py --runs runs --out docs/reports/data/2026-10-01.json
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List


def _outcomes(d: str) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(glob.glob(f"{d}/*__*/outcome.json")):
        try:
            out.append(json.loads(Path(f).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return out


def summarize_eval(d: str) -> Dict[str, Any]:
    outs = _outcomes(d)
    if not outs:
        return {}
    ok = [o for o in outs if not o["missing"]]
    wsum = sum(o["weight"] for o in outs)
    S = sum(o["reward"] * o["weight"] for o in outs) / wsum if wsum else 0.0
    per_task: Dict[str, List[float]] = collections.defaultdict(list)
    per_cat: Dict[str, List[float]] = collections.defaultdict(list)
    for o in outs:
        per_task[o["task_id"]].append(o["reward"])
        per_cat[o["task_id"].rsplit("-", 1)[0]].append(o["reward"])
    usage = [o["usage_tokens"] for o in ok if o.get("usage_tokens")]
    dur = [o["duration_s"] for o in ok if o.get("duration_s") is not None]
    # 과제 단위 표준오차(과제 평균 보상의 표본 표준편차 / √과제 수)
    task_means = [sum(v) / len(v) for v in per_task.values()]
    se = statistics.stdev(task_means) / math.sqrt(len(task_means)) if len(task_means) > 1 else 0.0
    return {
        "trials": len(outs), "missing": sum(o["missing"] for o in outs), "S": round(S, 4),
        "S_task_se": round(se, 4),
        "usage_tokens_mean": round(sum(usage) / len(usage)) if usage else None,
        "duration_s_mean": round(sum(dur) / len(dur), 2) if dur else None,
        "per_category": {k: round(sum(v) / len(v), 3) for k, v in sorted(per_cat.items())},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    runs = Path(args.runs)
    data: Dict[str, Any] = {"engine_comparison": {}, "replay_equivalence": {}, "evolution": {}, "dream": {}}
    for exp, suite in (("e1", "xgen-core"), ("e3", "xgen-hard")):
        for d in sorted(glob.glob(str(runs / exp / "*" / "*" / "*"))):
            label, engine, split = Path(d).parts[-3:]
            s = summarize_eval(d)
            if s:
                data["engine_comparison"].setdefault(suite, {})[f"{label}/{engine}/{split}"] = s
    for f in sorted(glob.glob(str(runs / "e2b" / "replay_*.json"))):
        r = json.loads(Path(f).read_text(encoding="utf-8"))
        label = Path(f).stem.split("_", 1)[1]
        data["replay_equivalence"][label] = {
            "tasks": len(r), "calls": sum(v["calls_geny"] for v in r.values()),
            "identical_calls": sum(v["identical_calls"] for v in r.values()),
            "answers_equal": sum(bool(v["answer_equal"]) for v in r.values()),
            "usage_equal": sum(v["usage_geny"] == v["usage_geny_rsi"] for v in r.values()),
            "reward_equal": sum(v["reward_geny"] == v["reward_geny_rsi"] for v in r.values()),
        }
    for run in sorted(glob.glob(str(runs / "evo-*"))):
        fr_path = Path(run) / "frontier.json"
        if not fr_path.exists():
            continue
        fr = json.loads(fr_path.read_text(encoding="utf-8"))
        dec = []
        for p in sorted(Path(run).glob("r*/decisions.json")):
            dj = json.loads(p.read_text(encoding="utf-8"))
            dec.append({"t": dj["t"], "winner": dj["winner"],
                        "decisions": [{k: x.get(k) for k in ("variant", "outcome", "reason_code", "S", "C", "delta_S", "delta_C",
                                                               "novelty", "components", "early_stopped")} for x in dj["decisions"]]})
        cal = Path(run) / "calibration.json"
        data["evolution"][Path(run).name] = {
            "trajectory": fr.get("trajectory"), "S_star": fr.get("S_star"),
            "incumbent": {k: fr["incumbent"].get(k) for k in ("t", "job", "S", "C", "harness_version")},
            "calibration": json.loads(cal.read_text(encoding="utf-8")) if cal.exists() else None,
            "rounds": dec,
        }
        held = {}
        for d in sorted(glob.glob(str(Path(run) / "jobs" / "heldout_*"))):
            s = summarize_eval(d)
            if s:
                held[Path(d).name] = s
        data["evolution"][Path(run).name]["heldout"] = held
    for run in sorted(glob.glob(str(runs / "dream-*"))):
        entry: Dict[str, Any] = {}
        for name in ("explore-pi1", "explore-portfolio"):
            p = Path(run) / f"pool-{name.split('-', 1)[1]}" / "iter0000" / "explore_summary.json"
            if p.exists():
                s = json.loads(p.read_text(encoding="utf-8"))
                entry[name] = {k: s.get(k) for k in ("S", "C", "missing", "n_tasks", "plan", "beta")}
                entry[name]["probes"] = sum(e["probes"] for e in s.get("episodes", []))
        cm = Path(run) / "cycle1" / "cycle_manifest.json"
        if cm.exists():
            c = json.loads(cm.read_text(encoding="utf-8"))
            entry["cycle1"] = {k: c.get(k) for k in ("status", "m_star", "promoted_label", "V_selection", "V_dev", "eligible",
                                                     "labels", "next_beta", "next_plan", "confirmation")}
        data["dream"][Path(run).name] = entry
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: list(v) if isinstance(v, dict) else v for k, v in data.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
