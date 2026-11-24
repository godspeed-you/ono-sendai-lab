# STATE — ono-sendai-lab

Version **0.1.0** (implementation of SPEC-001; no release promotion) · branch `implementation`

This file records actual progress and evidence. It adds no requirements.

## Scope

SPEC-001 AWS Emulator Foundation: `docs/specs/ono_sendai_lab_spec_v0.1.0.md`, adopted byte-identically, SHA-256 `ea257cb2da7da02a7ed6d9c3ac9f192ee3e14ad18a6b31cd512bb18c09862d0d`. Local backend only; the VM backend is design ([ADR-0005](adr/0005-backend-boundary-and-future-vm.md)).

## Code state

Final code: `c9f96f4`, code hash `ba729370c6d7fe8c…` (src, scenarios, locks, bin, scripts, tests, pyproject). Local and CI acceptance ran on this exact hash; later commits change only documentation and evidence, and `scripts/release-check.sh` verifies the committed evidence against the current code hash.

## Pinned dependencies

| Item | Value |
| --- | --- |
| AWS Emulator | 1.0.598 (release 2026.11.24, commit `fc05151cc5015eb457b31a79871539af51fb9fdd`) |
| Package | `aws-emulator_1.0.598-1_amd64.deb`, SHA-256 `b18a229dfb6b27acbe227bb907457f270e5417443a4d27a5c86ed9926e562fbb`, OpenPGP signature verified (publisher key fingerprint `98BC F336 A3BE 53B7 34E9 67C5 2E5B F49B D940 F624`) |
| Executable | 1,209,985,296 bytes, SHA-256 `950eeab681c2cd4e9167a8dbe8ad9a74e0d6ad51aad86dfb5660245bbbd1f20f` |
| Client | botocore 1.44.10 (hashed lock) |
| Gate tooling | pytest 9.2.0, ruff 0.17.6, jsonschema 4.26.0, typing-extensions 4.16.0 for Python < 3.13 (hashed lock) |

## Qualified host and effective controls

| Platform | Evidence |
| --- | --- |
| Ubuntu 26.04.1 LTS, kernel 7.0.0-43-generic, systemd 259, Python 3.14.4, distribution bubblewrap with the `bwrap-userns-restrict` AppArmor profile | local gate, acceptance and release-check |
| Ubuntu 24.04.5 LTS (GitHub `ubuntu-24.04`, kernel 7.0.0-1014-aws), Python 3.12.3 | CI gate and CI acceptance; the runner installs bubblewrap and sets `kernel.apparmor_restrict_unprivileged_userns=0` (operator decisions 2026-11-24, throwaway VM only) |

- Isolation: bubblewrap user/net/pid/ipc/uts/cgroup/mount namespaces, `lo` only, no routes (`192.0.2.1` → `ENETUNREACH`), tmpfs root with a read-only allowlist, no capabilities, `no_new_privs`, nested user namespaces disabled.
- Limits read from the kernel on both platforms: `cpu.max 100000 100000`, `memory.max 1073741824`, `memory.swap.max 0`, `memory.zswap.max 0`, `pids.max 512`; `RuntimeMaxSec` = absolute lifetime; single slot via per-user slot lock + unit census.

## Measurements (local acceptance on `c9f96f4`)

| Measurement | Idle (5 s after create) | After aws-minimal seed + 10 reads |
| --- | --- | --- |
| cgroup `memory.peak` | 264.5 MB | 266.8 MB (cap 1,073.7 MB) |
| swap | 0 | 0 |
| cgroup CPU time since start | — | 0.55 s |
| supervisor/worker RSS | 75 MB | — |

Emulator RSS including mapped binary pages ≈ 380–386 MB (earlier runs). Host CLI peak RSS per call: 50 MiB (`status`, `scenarios`). Durations: create 0.90 s, seed + 10 reads 0.91 s, reset with scenario 0.79 s, destroy 0.14 s. CI (24.04): create 0.83 s, reset with scenario 0.70 s, peak 263 MB. Disk: lab locations + checkout + run scratch ≈ 1.56 GB at rest; peak during acceptance ≈ 3.0 GB (fresh-install cycle adds 1.43 GB transiently); budget 5.0 GB, measured by ledger admission (no hard quota).

## Gates

| Gate | Result on `c9f96f4` | Duration |
| --- | --- | --- |
| `scripts/gate.sh` | passed (205 unit + contract tests, ruff, spec hash, traceability, secret scan) | 6 s |
| `scripts/acceptance.sh` | passed: 41 tests, rows A01–A19, cleanup verified, 15/15 operations verified, code hash unchanged during the run | 238 s |
| `scripts/release-check.sh` | see the final run record in the commit that follows this one | — |

Evidence: [acceptance-evidence.json](evidence/acceptance-evidence.json) (local) · [ci-acceptance-evidence-45496461854.json](evidence/ci-acceptance-evidence-45496461854.json) (CI, verbatim artifact) · [ci-acceptance-evidence-45495408663.json](evidence/ci-acceptance-evidence-45495408663.json) (first CI acceptance, verbatim; produced before evidence carried its origin, so it says `not_ci_evidence: true` although it came from CI) · [operation-compatibility.json](contracts/operation-compatibility.json) · [doc-review.md](evidence/doc-review.md) · [traceability.json](contracts/traceability.json).

## CI status

Local evidence is never reported as CI evidence.

| Run | Commit | gate (ubuntu-24.04, Python 3.12) | acceptance |
| --- | --- | --- | --- |
| [45437926431](https://github.com/godspeed-you/ono-sendai-lab/actions/runs/45437926431) | `63d5843` | failed: `referencing` needs `typing-extensions` on Python 3.12 (fixed, regression test added) | skipped (needs gate) |
| [45438803336](https://github.com/godspeed-you/ono-sendai-lab/actions/runs/45438803336) | `a66372d` | failed: review record missing (A18) | skipped |
| [45439750008](https://github.com/godspeed-you/ono-sendai-lab/actions/runs/45439750008) | `ddeb52e` | **passed** | **blocked**: runner qualification failed, hosted `ubuntu-24.04` lacks `bubblewrap` (systemd user manager, gpg, dpkg-deb present) |
| [45440713090](https://github.com/godspeed-you/ono-sendai-lab/actions/runs/45440713090) | `aee03a2` | **passed** | **blocked** (same runner blocker) |
| [45494341300](https://github.com/godspeed-you/ono-sendai-lab/actions/runs/45494341300) | `b73757d` | **passed** | **blocked**: bubblewrap installed (operator-approved), qualification passed; bootstrap preflight refused (exit 4, `isolation_unavailable`): `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` — the AppArmor user-namespace restriction of the 24.04 image; the lab failed closed |
| [45495408663](https://github.com/godspeed-you/ono-sendai-lab/actions/runs/45495408663) | `d41b156` | **passed** | **passed**: 41 tests, rows A01–A19, Ubuntu 24.04.5, Python 3.12.3 |
| [45496461854](https://github.com/godspeed-you/ono-sendai-lab/actions/runs/45496461854) | `c9f96f4` | **passed** | **passed**: 41 tests, rows A01–A19, cleanup verified, code hash `ba729370…` (same as local) |

CI runs the same scripts. Its two privileged steps (install bubblewrap; set `kernel.apparmor_restrict_unprivileged_userns=0`) were approved by the operator on 2026-11-24 for the throwaway runner only. A Ubuntu 24.04 developer host needs its administrator to allow bwrap user namespaces before the lab can run there; the lab never changes this itself.

## Issues (milestone v0.1.0)

| Issue | Disposition |
| --- | --- |
| #1 bootstrap/provenance | verified (A01), closed |
| #2 containment/limits/disk | verified (A02, A04, A11, A14), closed |
| #3 lifecycle | verified (A09, A12–A15), closed |
| #4 CLI contract | verified (A16), closed |
| #5 managed client | verified (A03, A07, A10), closed |
| #6 fixtures/compatibility | verified (A05–A08, A17), closed |
| #7 security | verified (A10, A11, A16; threat model reviewed), closed |
| #8 docs | verified (A18, independent review), closed |
| #9 gates/CI | verified: local gates and release-check, CI gate and CI acceptance green on `c9f96f4`; closed |

## Findings (open)

- **Licensing:** resolved 2026-11-24 — Apache-2.0 (operator pointed to the project's intended long-term licence in core `docs/strategy/cncf-readiness.md` §3; ADR-0008).
- **Ubuntu 24.04 hosts:** acceptance passes in CI with the AppArmor user-namespace restriction lifted; developer hosts on 24.04 need an administrator to allow bwrap user namespaces.
- **Upstream doc difference:** wrong keys yield `InvalidClientTokenId` on 1.0.598; the user guide documents `AccessDenied` (recorded in the compatibility manifest).
- **Process note:** the foundation (e27aa02) was written before its tests; tests, contracts and ADRs followed in the next commits. Every later defect fix came with a regression test.

## Continuation

See [AGENTS.md](../AGENTS.md) → Continuation protocol. Next action: release promotion (merge to `main`, tag v0.1.0) is a separate, explicit operator step.
