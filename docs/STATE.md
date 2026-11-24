# STATE — ono-sendai-lab

Version **0.1.0** (implementation of SPEC-001; no release promotion) · branch `implementation`

This file records actual progress and evidence. It adds no requirements.

## Scope

SPEC-001 AWS Emulator Foundation: `docs/specs/ono_sendai_lab_spec_v0.1.0.md`, adopted byte-identically, SHA-256 `ea257cb2da7da02a7ed6d9c3ac9f192ee3e14ad18a6b31cd512bb18c09862d0d`. Local backend only; the VM backend is design (ADR-0005).

## Pinned dependencies

| Item | Value |
| --- | --- |
| AWS Emulator | 1.0.598 (release 2026.11.24, commit `fc05151cc5015eb457b31a79871539af51fb9fdd`) |
| Package | `aws-emulator_1.0.598-1_amd64.deb`, SHA-256 `b18a229dfb6b27acbe227bb907457f270e5417443a4d27a5c86ed9926e562fbb`, OpenPGP signature verified (publisher key fingerprint `98BC F336 A3BE 53B7 34E9 67C5 2E5B F49B D940 F624`) |
| Executable | 1,209,985,296 bytes, SHA-256 `950eeab681c2cd4e9167a8dbe8ad9a74e0d6ad51aad86dfb5660245bbbd1f20f` |
| Client | botocore 1.44.10 (hashed lock) |
| Gate tooling | pytest 9.2.0, ruff 0.17.6, jsonschema 4.26.0 (hashed lock) |

## Qualified host

Ubuntu 26.04.1 LTS, kernel 7.0.0-43-generic, x86_64, systemd 259, bubblewrap with the `bwrap-userns-restrict` AppArmor profile, Python 3.14.4. Ubuntu 24.04 / Python 3.12: CI gate only so far (ADR-0006).

Effective isolation: bubblewrap user/net/pid/ipc/uts/cgroup/mount namespaces, `lo` only, no routes, tmpfs root with read-only allowlist, no capabilities, `no_new_privs`. Effective limits (read from the kernel): `cpu.max 100000 100000`, `memory.max 1073741824`, `memory.swap.max 0`, `memory.zswap.max 0`, `pids.max 512`, `RuntimeMaxSec` = absolute lifetime.

## Gates

| Gate | Status | Notes |
| --- | --- | --- |
| `scripts/gate.sh` | in progress | |
| `scripts/acceptance.sh` | in progress | A01–A17 passed individually during development |
| `scripts/release-check.sh` | pending | |

## CI status

Local evidence is never reported as CI evidence.

| Run | Commit | gate (ubuntu-24.04, Python 3.12) | acceptance |
| --- | --- | --- | --- |
| [45437926431](https://github.com/godspeed-you/ono-sendai-lab/actions/runs/45437926431) | `63d5843` | failed: `referencing` needs `typing-extensions` on Python 3.12; the dev lock had been resolved on 3.14 (fixed with a lock-closure regression test) | skipped (needs gate) |

## Issues (milestone v0.1.0)

#1 bootstrap · #2 containment/limits · #3 lifecycle · #4 CLI contract · #5 managed client · #6 fixtures · #7 security · #8 docs · #9 gates/CI — all open.

## Findings (found, not yet filed)

- Licensing: the repository has no licence and the operator has not specified one. **Unresolved publication prerequisite**; no licence was guessed.
- Upstream documentation says wrong keys yield `AccessDenied`; 1.0.598 returns `InvalidClientTokenId` (recorded in the compatibility manifest).

## Continuation

See AGENTS.md → Continuation protocol.
