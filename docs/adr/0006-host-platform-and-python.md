# ADR-0006: Qualified host platform and Python runtime

Status: accepted · 2026-11-24 · Requirements: LAB-010, LAB-032

## Context

SPEC-001 names Ubuntu 24.04 LTS x86_64 as the primary acceptance platform and Python 3.12 as the reference runtime. The development machine available for this run is **Ubuntu 26.04.1 LTS, kernel 7.0.0, x86_64, systemd 259, bubblewrap from the distribution, Python 3.14.4**; it has no Python 3.12 interpreter, and installing one would need sudo or an extra download the run is not authorised to make.

## Decision

- The code targets Python ≥ 3.12 (ruff `target-version = py312`, no 3.13+ syntax) and depends only on the standard library on the host side.
- **Local qualification** (full gate and real acceptance) happens on Ubuntu 26.04 / Python 3.14. This host is recorded as the tested configuration.
- **Ubuntu 24.04 / Python 3.12** is covered by CI on GitHub's `ubuntu-24.04` image: the gate job (static checks, unit and contract tests) and the real acceptance job. On 24.04 the AppArmor user-namespace restriction keeps the distribution's bwrap from bringing up loopback; the CI runner lifts it with `kernel.apparmor_restrict_unprivileged_userns=0` (operator decision 2026-11-24, ephemeral VM only). First full CI acceptance: run 45495408663 on Ubuntu 24.04.5, Python 3.12.3, all rows A01–A19 passed. A 24.04 developer host needs its administrator to allow bwrap user namespaces (an AppArmor profile for `/usr/bin/bwrap`, or the sysctl); the lab never changes this itself.
- Other distributions and architectures are unqualified.

## Consequences

Both platforms have real acceptance evidence: 26.04 locally (with the shipped `bwrap-userns-restrict` profile) and 24.04 in CI (with the restriction lifted on the throwaway runner).
