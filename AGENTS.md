# AGENTS.md — working agreement

The adopted specification `docs/specs/ono_sendai_lab_spec_v0.1.0.md` (SHA-256 `ea257cb2da7da02a7ed6d9c3ac9f192ee3e14ad18a6b31cd512bb18c09862d0d`) outranks this file. Authority: spec → machine-readable contracts → ADRs → acceptance definition → tests → implementation.

## Safety constraints (always)

1. Never contact real AWS APIs, never read or inspect real AWS credential/config files, never put credentials in argv, logs, commits, issues or evidence.
2. No sudo, no package installation with elevated privileges, no sysctl/AppArmor/firewall/network/global-service changes, no edits to SSH, `~/.aws`, other repositories or workspaces.
3. No Rust/Cargo, no emulator source build, no Ono build, no Docker image build, no Kubernetes.
4. At most one active emulator generation; every generation runs contained (bubblewrap) and capped (one CPU, 1 GiB, no swap). Never weaken isolation or a limit to make something pass; fail closed and report.
5. Disk: at most 5,000,000,000 bytes for the whole run and lab, measured by the ledger (`devtools admit`). Never delete files outside the lab locations and the checkout.
6. Another agent may be building Ono on the same machine: run tests sequentially, keep heavy steps under `nice`, never stop processes that are not lab units.
7. Work only on the `implementation` branch; push only to this repository's origin. No merge to `main`, no tag, no release without explicit promotion.

## Continuation protocol

1. Read `docs/STATE.md` (scope, commit, gates, open findings) and `git log --oneline -20`.
2. `bin/aws-lab status` and `systemctl --user list-units 'ono-sendai-lab-*'`: destroy anything a previous run left.
3. `bin/aws-lab doctor` must be safe before any real run.
4. Change in this order: contract/ADR if behaviour changes → failing test → implementation → focused tests → `scripts/gate.sh`.
5. Real runs: `scripts/acceptance.sh` (sequential, fresh state, cleans up). Before release: `scripts/release-check.sh` on a clean worktree.
6. Update `docs/STATE.md` with what was verified and what is open; close GitHub issues only with commit and test evidence (`gh issue close N -c …`).
