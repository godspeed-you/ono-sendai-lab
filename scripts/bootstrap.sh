#!/usr/bin/env bash
# Install, verify or uninstall ono-sendai-lab without privileges (LAB-011).
#   scripts/bootstrap.sh --plan        preflight + plan, no changes
#   scripts/bootstrap.sh [--dev]       install / verify (idempotent)
#   scripts/bootstrap.sh --uninstall   remove every lab-owned location
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
exec /usr/bin/python3 -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from ono_sendai_lab.bootstrap import main; sys.exit(main())' "$ROOT/src" "$@"
