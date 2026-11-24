#!/usr/bin/env bash
# Real acceptance (LAB-091): actual emulator, namespaces and cgroups, run
# sequentially in fresh lab-owned state. Refuses to start while any lab
# generation is active; always cleans up what it created.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"
require_dev_venv acceptance
START=$(date +%s)
export PYTHONDONTWRITEBYTECODE=1

if [ -n "$(active_lab_units)" ]; then
  echo "acceptance: refused: a lab generation is active ($(active_lab_units | tr '\n' ' ')); destroy it first" >&2
  exit 4
fi
echo "acceptance: preflight (doctor)"
if ! bin/aws-lab doctor >/dev/null; then
  echo "acceptance: refused: aws-lab doctor reports an unsafe or incomplete host (see: bin/aws-lab doctor)" >&2
  exit 4
fi
echo "acceptance: disk admission"
devtools admit 1700000000   # fresh install cycle (1.4 GB transient) + run state

mkdir -p -m 700 "$LAB_CACHE/acceptance"
# Remove earlier run directories (a failed run is kept until the next run).
find "$LAB_CACHE/acceptance" -mindepth 1 -maxdepth 1 -type d -name 'run-*' -mmin +0 -exec rm -rf {} + 2>/dev/null || true
RUN="$LAB_CACHE/acceptance/run-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p -m 700 "$RUN"
export AWS_LAB_ACCEPTANCE_DIR="$RUN"
RC=0

cleanup() {
  local state="$RUN/state"
  if [ -d "$state/ono-sendai-lab/sandboxes" ]; then
    for rec in "$state"/ono-sendai-lab/sandboxes/*/record.json; do
      [ -f "$rec" ] || continue
      name=$("$DEV_PY" -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$rec")
      XDG_STATE_HOME="$state" bin/aws-lab --json destroy "$name" >/dev/null 2>&1 || true
    done
  fi
  rm -rf "$RUN/fresh" "$RUN/sentinel-home" "$RUN/tmp"
}
trap 'cleanup; echo "acceptance: interrupted" >&2; exit 130' INT TERM

echo "acceptance: running tests/acceptance sequentially (run dir under the lab cache)"
set +e
timeout 3600 nice -n 10 "$DEV_PY" -I -m pytest tests/acceptance -p no:cacheprovider -q --basetemp "$RUN/tmp" -o console_output_style=classic
RC=$?
set -e
cleanup

echo "acceptance: verifying cleanup"
LEFT_UNITS=$(active_lab_units | tr '\n' ' ')
RUNTIME_LEFT=$(find "${XDG_RUNTIME_DIR}/ono-sendai-lab" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)
CREDS_LEFT=$(find "$RUN" "${XDG_RUNTIME_DIR}/ono-sendai-lab" "$DATA_HOME/ono-sendai-lab" -name emulator_credentials 2>/dev/null | wc -l)
RECORDS_LEFT=$(find "$RUN/state" -name record.json 2>/dev/null | wc -l)
VERIFIED=false
if [ -z "$LEFT_UNITS" ] && [ "$RUNTIME_LEFT" -eq 0 ] && [ "$CREDS_LEFT" -eq 0 ] && [ "$RECORDS_LEFT" -eq 0 ]; then VERIFIED=true; fi
cat > "$RUN/cleanup.json" <<JSON
{"verified": $VERIFIED, "active_lab_units": "$LEFT_UNITS", "runtime_entries": $RUNTIME_LEFT, "credential_files": $CREDS_LEFT, "records": $RECORDS_LEFT}
JSON
echo "{\"acceptance_seconds\": $(( $(date +%s) - START ))}" > "$RUN/durations.json"

mkdir -p docs/evidence/raw
EVIDENCE="docs/evidence/raw/acceptance-$(basename "$RUN" | sed 's/^run-//').json"
devtools build-evidence "$RUN" "$EVIDENCE"
cp "$EVIDENCE" docs/evidence/raw/acceptance-latest.json
COMPAT_RC=0
if [ -f "$RUN/operations.jsonl" ]; then devtools build-compat "$RUN" || COMPAT_RC=$?; else COMPAT_RC=1; fi

if [ "$RC" -ne 0 ] || [ "$VERIFIED" != true ] || [ "$COMPAT_RC" -ne 0 ]; then
  echo "acceptance: FAILED (pytest=$RC cleanup_verified=$VERIFIED compat=$COMPAT_RC); run dir kept: $RUN" >&2
  exit 1
fi
rm -rf "$RUN"
echo "acceptance: passed in $(( $(date +%s) - START ))s; evidence $EVIDENCE"
