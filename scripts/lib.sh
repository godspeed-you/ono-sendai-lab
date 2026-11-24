# Shared helpers for the gate scripts. Sourced, never executed.
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
CACHE_HOME=${XDG_CACHE_HOME:-$HOME/.cache}
DEV_VENV="$DATA_HOME/ono-sendai-lab/venv-dev"
DEV_PY="$DEV_VENV/bin/python"
LAB_CACHE="$CACHE_HOME/ono-sendai-lab"

require_dev_venv() {
  if [ ! -x "$DEV_PY" ]; then
    echo "$1: the hashed dev toolchain is missing; run scripts/bootstrap.sh --dev (nothing is installed implicitly)" >&2
    exit 4
  fi
}

devtools() {
  PYTHONDONTWRITEBYTECODE=1 "$DEV_PY" -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from ono_sendai_lab.devtools import main; sys.exit(main())' "$ROOT/src" "$@"
}

# Prints active lab units. If the user manager cannot be queried it prints a
# marker instead, so callers that require "no units" fail closed.
active_lab_units() {
  local out
  if ! out=$(systemctl --user list-units --all --plain --no-legend --state=active,activating,deactivating 'ono-sendai-lab-*' 2>/dev/null); then
    echo "systemd-user-manager-unreachable"
    return 0
  fi
  printf '%s\n' "$out" | awk 'NF {print $1}' | grep -v '^ono-sendai-lab-probe' || true
}

# Prints the PIDs of aws-emulator processes of this user (there must be none after a run).
lab_emulator_pids() {
  pgrep -u "$(id -u)" -x aws-emulator || true
}

# Generation directories left in the runtime location (the slot lock file is not one).
runtime_leftovers() {
  find "${XDG_RUNTIME_DIR}/ono-sendai-lab" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l
}
