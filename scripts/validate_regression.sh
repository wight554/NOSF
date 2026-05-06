#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

cd "$REPO"

echo "=== Generate Config ==="
python3 scripts/gen_config.py

echo "=== Firmware Build ==="
ninja -C build_local

echo "=== Python Syntax ==="
python3 -m py_compile scripts/*.py

echo "=== Diff Hygiene ==="
git diff --check

echo "=== Static Regression Gate Passed ==="
echo "Run the hardware validation cases in TEST_CASES.md for motion, sync, toolchange, or RELOAD changes."