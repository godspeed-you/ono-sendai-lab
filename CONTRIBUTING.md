# Contributing

## Setup

```sh
scripts/bootstrap.sh --dev        # pinned emulator, hashed runtime and dev venvs
bin/aws-lab doctor
```

## Gates

| Script | Runs | Needs |
| --- | --- | --- |
| `scripts/gate.sh` | shell/Python syntax, ruff format + lint, spec hash, traceability, secret scan, unit and contract tests | dev venv |
| `scripts/acceptance.sh` | real emulator, namespaces and cgroups, A01–A19, sequential, fresh state, cleanup verification, evidence | installed lab, idle slot |
| `scripts/release-check.sh` | both, on a clean worktree, plus evidence, compatibility manifest and cleanup checks; prints `release-check: ono-sendai-lab is release-ready` | as above |

Run the narrowest test first (`$XDG_DATA_HOME/ono-sendai-lab/venv-dev/bin/python -m pytest tests/unit/test_x.py -k name`, default `~/.local/share/…`); acceptance subsets run with `AWS_LAB_ACCEPTANCE_DIR` set to a fresh 0700 directory under `$XDG_CACHE_HOME/ono-sendai-lab/acceptance/`.

## Rules

- Tests exercise real behaviour. Fakes only at external boundaries (botocore client, backend, systemd) in unit tests; acceptance always uses the real mechanisms. Never weaken, skip or delete a test to make a gate pass; never hard-code expected output.
- Every requirement change starts in a new specification; ambiguity is resolved in an ADR.
- Keep `docs/contracts/*` in step with code (contract tests compare them).
- Add a regression test with every defect fix.
- Commits on `implementation`; reference issues; close them explicitly with evidence.
