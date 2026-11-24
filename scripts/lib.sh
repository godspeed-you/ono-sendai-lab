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

active_lab_units() {
  systemctl --user list-units --all --plain --no-legend --state=active,activating,deactivating 'ono-sendai-lab-*' 2>/dev/null \
    | awk '{print $1}' | grep -v '^ono-sendai-lab-probe' || true
}
