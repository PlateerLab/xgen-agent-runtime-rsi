#!/usr/bin/env bash
# 실험 1(엔진 비교)이 끝나면 그 시행을 기준선으로 RRSI 진화를 시작한다.
#   experiments/start_evo_after_e1.sh LABEL POLICY_JSON RRSI_JSON SUITE_DIR [E1_OUT]
# 기준선 H0 = E1 의 geny-rsi evolve 시행, δ 보정의 두 번째 기준 평가 = E1 의 geny evolve 시행(두 엔진의 요청이 바이트 단위로
# 같음을 재생 동등성으로 실측했다 — 보고서 실험 2).
set -euo pipefail
LABEL=$1; POLICY=$2; CONFIG=$3; SUITE=$4; E1=${5:-runs/p1}
RSI=${RSI:-.venv-indep/bin/rsi}
until [ -f "$E1/$LABEL/geny-rsi/evolve/eval.json" ] && [ -f "$E1/$LABEL/geny/evolve/eval.json" ]; do sleep 30; done
$RSI evolve init "runs/evo-$LABEL" --suite "$SUITE" --policy "$POLICY" --config "$CONFIG" --name "$LABEL-pro" > /dev/null
mkdir -p "runs/evo-$LABEL/jobs/base" "runs/evo-$LABEL/jobs/base_r2"
cp -r "$E1/$LABEL/geny-rsi/evolve/"*__* "runs/evo-$LABEL/jobs/base/"
cp -r "$E1/$LABEL/geny/evolve/"*__* "runs/evo-$LABEL/jobs/base_r2/"
exec $RSI evolve run "runs/evo-$LABEL" --T 5
