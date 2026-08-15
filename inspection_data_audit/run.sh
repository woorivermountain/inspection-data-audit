#!/bin/sh
set -eu

DATA_ROOT=${1:-/Users/woorivermountain/Desktop/data}
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

"$PYTHON_BIN" -m unittest discover -s "$SCRIPT_DIR/tests" -v

echo "완료: $OUTPUT_DIR"
