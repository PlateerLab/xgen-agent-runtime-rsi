#!/usr/bin/env bash
# 실험 4 마무리 — 보류 분할(판정에 쓰지 않은 과제)에서 geny / geny-rsi H0 / geny-rsi H* 를 k=4 로 잰다.
#
#   experiments/heldout_final.sh LABEL POLICY_JSON SUITE_DIR [K]
#
# runs/e3/<LABEL>/{geny,geny-rsi}/heldout 의 기존 시행(0,1)은 재사용하고 2..K-1 만 더 돈다.
set -euo pipefail
LABEL=$1; POLICY=$2; SUITE=$3; K=${4:-4}
PY=${PY:-.venv/bin/python}; RSI=${RSI:-.venv/bin/rsi}
$PY experiments/compare_engines.py --policy "$LABEL=$POLICY" --suite "$SUITE" --out runs/e3 --k "$K" --splits heldout --parallel 4
$RSI evolve heldout "runs/evo-$LABEL" final --split heldout --k "$K"
$RSI evolve export "runs/evo-$LABEL" "runs/evo-$LABEL/H_star"
$RSI harness diff src/xgen_rsi/harnesses/h0 "runs/evo-$LABEL/H_star"
