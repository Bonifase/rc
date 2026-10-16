#!/usr/bin/env bash
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi
"$PYTHON_BIN" -m pytest -q \
  tests/test_hybrid_adapter_offline.py \
  tests/test_metadata_report.py \
  tests/test_acceptance_gate_t15_t17.py
