#!/usr/bin/env bash
# Release check (LAB-091, LAB-100): gate + acceptance on the final checkout,
# evidence, documentation and cleanup. Prints the release-ready marker only
# on full success. Local evidence only; CI status is reported separately.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"
require_dev_venv release-check
START=$(date +%s)
fail() { echo "release-check: FAILED: $*" >&2; exit 1; }

[ -z "$(git status --porcelain)" ] || fail "worktree is not clean"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
grep -q "__version__ = \"$VERSION\"" src/ono_sendai_lab/__init__.py || fail "package version differs from pyproject ($VERSION)"
grep -q "$VERSION" docs/STATE.md || fail "docs/STATE.md does not name version $VERSION"

GATE_START=$(date +%s)
scripts/gate.sh || fail "gate"
GATE_S=$(( $(date +%s) - GATE_START ))
ACC_START=$(date +%s)
scripts/acceptance.sh || fail "acceptance"
ACC_S=$(( $(date +%s) - ACC_START ))

devtools evidence docs/evidence/raw/acceptance-latest.json || fail "fresh acceptance evidence"
devtools evidence docs/evidence/acceptance-evidence.json || fail "committed acceptance evidence does not match this code (copy the fresh evidence and commit it)"
git diff --quiet -- docs/contracts/operation-compatibility.json || fail "operation compatibility manifest changed during acceptance; commit the regenerated manifest"
[ -z "$(active_lab_units)" ] || fail "lab units still active"
[ "$(find "${XDG_RUNTIME_DIR}/ono-sendai-lab" -mindepth 1 2>/dev/null | wc -l)" -eq 0 ] || fail "lab runtime files remain"
[ "$(find "$DATA_HOME/ono-sendai-lab" "$LAB_CACHE" "${XDG_RUNTIME_DIR}/ono-sendai-lab" -name emulator_credentials 2>/dev/null | wc -l)" -eq 0 ] || fail "credential files remain"
[ -z "$(git status --porcelain)" ] || fail "worktree changed during the release check"
echo "release-check: gate ${GATE_S}s, acceptance ${ACC_S}s, total $(( $(date +%s) - START ))s"
echo "release-check: ono-sendai-lab is release-ready"
