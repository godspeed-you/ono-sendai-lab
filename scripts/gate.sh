#!/usr/bin/env bash
# Static and unit gate (LAB-091): syntax, format, lint, unit + contract tests,
# spec hash, traceability and secret scanning. No emulator, no network.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"
require_dev_venv gate
START=$(date +%s)
TMP=$(mktemp -d "${LAB_CACHE}/tmp/gate.XXXXXX" 2>/dev/null || { mkdir -p -m 700 "$LAB_CACHE/tmp" && mktemp -d "$LAB_CACHE/tmp/gate.XXXXXX"; })
trap 'rm -rf "$TMP"' EXIT INT TERM
export PYTHONDONTWRITEBYTECODE=1

echo "gate: shell syntax"
for f in scripts/*.sh; do bash -n "$f"; done
sh -n bin/aws-lab
echo "gate: python syntax"
timeout 120 "$DEV_PY" -I -c 'import ast, pathlib, sys
for p in sorted(pathlib.Path(".").glob("src/**/*.py")) + sorted(pathlib.Path(".").glob("tests/**/*.py")):
    ast.parse(p.read_text(), str(p))
print("ok")'
echo "gate: format and lint (ruff)"
timeout 120 "$DEV_VENV/bin/ruff" format --check --quiet src tests
timeout 120 "$DEV_VENV/bin/ruff" check --quiet src tests
echo "gate: spec hash"
devtools spec
echo "gate: traceability"
devtools traceability
echo "gate: secret scan"
devtools secret-scan
echo "gate: unit and contract tests"
timeout 900 nice -n 10 "$DEV_PY" -I -m pytest tests/unit tests/contract -q -p no:cacheprovider --basetemp "$TMP/pytest"
echo "gate: passed in $(( $(date +%s) - START ))s"
