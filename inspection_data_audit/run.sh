#!/bin/sh
set -eu

DATA_ROOT=${1:-./local_data}
PYTHON_BIN=${PYTHON_BIN:-python3}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUTPUT_DIR="$SCRIPT_DIR/outputs"

"$PYTHON_BIN" "$SCRIPT_DIR/audit_current_data.py" \
  --data-root "$DATA_ROOT" \
  --output-dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$SCRIPT_DIR/simulate_pathologies.py" \
  --seeds 100 \
  --events 300 \
  --output-dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$SCRIPT_DIR/performance_gap_experiment.py" \
  --seeds 100 \
  --events 300 \
  --output-dir "$OUTPUT_DIR"

if [ -f "$SCRIPT_DIR/external_data/siemens/dataset.csv" ] && \
   [ -f "$SCRIPT_DIR/external_data/siemens/mapping.json" ]; then
  "$PYTHON_BIN" "$SCRIPT_DIR/external_validate_siemens.py" \
    --dataset "$SCRIPT_DIR/external_data/siemens/dataset.csv" \
    --mapping "$SCRIPT_DIR/external_data/siemens/mapping.json" \
    --output-dir "$OUTPUT_DIR"
  "$PYTHON_BIN" "$SCRIPT_DIR/temporal_followup_siemens.py" \
    --dataset "$SCRIPT_DIR/external_data/siemens/dataset.csv" \
    --mapping "$SCRIPT_DIR/external_data/siemens/mapping.json" \
    --output-dir "$OUTPUT_DIR" \
    --skip-hash
else
  echo "[건너뜀] Siemens 외부 데이터 없음: download_siemens.py를 먼저 실행하세요"
fi

"$PYTHON_BIN" -m unittest discover -s "$SCRIPT_DIR/tests" -v

echo "완료: $OUTPUT_DIR"
