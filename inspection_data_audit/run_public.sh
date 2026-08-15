#!/bin/sh
set -eu

PYTHON_BIN=${PYTHON_BIN:-python3}
PAPER_SEEDS=${PAPER_SEEDS:-10}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_DIR="$SCRIPT_DIR/external_data/siemens"
OUTPUT_DIR="$SCRIPT_DIR/outputs"
DATASET="$DATA_DIR/dataset.csv"
MAPPING="$DATA_DIR/mapping.json"

if [ ! -f "$DATASET" ] || [ ! -f "$MAPPING" ]; then
  echo "[중단] Siemens 공개 데이터가 없습니다. 먼저 다음 명령을 실행하세요:" >&2
  echo "  $PYTHON_BIN \"$SCRIPT_DIR/download_siemens.py\" --output-dir \"$DATA_DIR\" --accept-license" >&2
  exit 2
fi

"$PYTHON_BIN" -c "import pandas, sklearn; print('[환경]', 'pandas', pandas.__version__, 'scikit-learn', sklearn.__version__)"

"$PYTHON_BIN" "$SCRIPT_DIR/simulate_pathologies.py" \
  --seeds 100 \
  --events 300 \
  --output-dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$SCRIPT_DIR/performance_gap_experiment.py" \
  --seeds 100 \
  --events 300 \
  --output-dir "$OUTPUT_DIR"

# 첫 외부 실행에서 공식 크기와 SHA-256을 검증한다.
"$PYTHON_BIN" "$SCRIPT_DIR/external_validate_siemens.py" \
  --dataset "$DATASET" \
  --mapping "$MAPPING" \
  --output-dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$SCRIPT_DIR/temporal_followup_siemens.py" \
  --dataset "$DATASET" \
  --mapping "$MAPPING" \
  --output-dir "$OUTPUT_DIR" \
  --skip-hash

"$PYTHON_BIN" "$SCRIPT_DIR/nonlinear_feasibility_siemens.py" \
  --dataset "$DATASET" \
  --mapping "$MAPPING" \
  --output-dir "$OUTPUT_DIR" \
  --skip-hash

"$PYTHON_BIN" "$SCRIPT_DIR/paper_protocol_reconstruction_siemens.py" \
  --dataset "$DATASET" \
  --mapping "$MAPPING" \
  --output-dir "$OUTPUT_DIR" \
  --skip-hash \
  --seeds "$PAPER_SEEDS"

"$PYTHON_BIN" -m unittest discover -s "$SCRIPT_DIR/tests" -v

echo "완료: $OUTPUT_DIR"
