#!/usr/bin/env bash
# 실험 5 — Dream-RSI: 라이브 탐색(π₁ vs portfolio) → world 풀 → 정책 개발 사이클 → RRSI 온라인 확인.
#
#   experiments/dream_experiment.sh LABEL POLICY_JSON SUITE_DIR OUT_DIR DELTA [EVAL_JOB_DIR]
#
# POLICY_JSON 은 정책 π 이자 정책 개발 역할 모델(같은 모델 — 재귀적 자기 개선). 키가 들어 있으니 저장소 밖에 둔다.
set -euo pipefail
LABEL=$1; POLICY=$2; SUITE=$3; OUT=$4; DELTA=$5; EVALJOB=${6:-}
RSI=${RSI:-.venv/bin/rsi}
PY=${PY:-.venv/bin/python}
GRID="--plan fixed --branches 3 --refine 2 --W 3"
# 범주마다 첫 과제(evolve 분할) 8개
TASKS_SPLIT=dream8
$PY - "$SUITE" <<'PY'
import json, sys, pathlib
root = pathlib.Path(sys.argv[1]); meta = json.loads((root / "suite.json").read_text())
ids = [i for i in meta["splits"]["evolve"] if i.endswith("-0")]
meta["splits"]["dream8"] = ids
(root / "suite.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
print("dream8:", ids)
PY
mkdir -p "$OUT"
$RSI dream explore "$OUT/pool-pi1" --suite "$SUITE" --split $TASKS_SPLIT --policy "$POLICY" \
     --explorer builtin:parallel_refine --iteration 0 $GRID | tee "$OUT/explore-pi1.json"
$RSI dream explore "$OUT/pool-portfolio" --suite "$SUITE" --split $TASKS_SPLIT --policy "$POLICY" \
     --explorer builtin:portfolio --iteration 0 $GRID | tee "$OUT/explore-portfolio.json"
EXTRA=()
if [ -n "$EVALJOB" ]; then EXTRA=(--eval-job "h0=$EVALJOB"); fi
$RSI dream build-worlds "$OUT/worlds.json" --trace-pool "$OUT/pool-pi1" --suite "$SUITE" "${EXTRA[@]}"
$PY - "$OUT" <<'PY'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
# portfolio 탐색 트리도 같은 풀에 더한다(서로 다른 탐색 정책이 만든 트리 — 재생 world 를 넓힌다)
from xgen_rsi.discovery import Episode, worlds_from_episodes
from xgen_rsi.dream.world import WorldPool
pool = WorldPool.load(out / "worlds.json")
eps = [Episode.from_json(json.loads(p.read_text())) for p in sorted((out / "pool-portfolio").glob("iter*/*/episode.json"))]
extra = [w for w in worlds_from_episodes(eps)]
renamed = []
for w in extra:
    d = w.to_json(); d["world_id"] = d["world_id"].replace("live:", "live-portfolio:")
    renamed.append(type(w).from_json(d))
pool = pool.add(*renamed)
pool.save(out / "worlds.json")
print("worlds:", len(pool))
PY
$RSI dream cycle "$OUT/cycle1" --worlds "$OUT/worlds.json" --incumbent builtin:parallel_refine --iteration 1 \
     --llm "$POLICY" --M 3 --trace-pool "$OUT/pool-pi1" \
     --confirm-suite "$SUITE" --confirm-split $TASKS_SPLIT --policy "$POLICY" --delta "$DELTA" $GRID | tee "$OUT/cycle1.json"
