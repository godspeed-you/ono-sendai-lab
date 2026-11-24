# ADR-0006: Qualified host platform and Python runtime

Status: accepted · 2026-11-24 · Requirements: LAB-010, LAB-032

## Context

SPEC-001 names Ubuntu 24.04 LTS x86_64 as the primary acceptance platform and Python 3.12 as the reference runtime. The development machine available for this run is **Ubuntu 26.04.1 LTS, kernel 7.0.0, x86_64, systemd 259, bubblewrap from the distribution, Python 3.14.4**; it has no Python 3.12 interpreter, and installing one would need sudo or an extra download the run is not authorised to make.

## Decision

- The code targets Python ≥ 3.12 (ruff `target-version = py312`, no 3.13+ syntax) and depends only on the standard library on the host side.
- **Local qualification** (full gate and real acceptance) happens on Ubuntu 26.04 / Python 3.14. This host is recorded as the tested configuration.
- **Ubuntu 24.04 / Python 3.12** is covered by the CI gate job on GitHub's `ubuntu-24.04` image (system Python 3.12: static checks, unit and contract tests). Real acceptance on 24.04 needs a qualified runner (docs/operations/ci-runner.md); until such a run exists, Ubuntu 24.04 acceptance is reported as **not yet qualified**, with the CI result stated separately in docs/STATE.md.
- Other distributions and architectures are unqualified.

## Consequences

The primary acceptance platform named by the spec has unit-level evidence only until an acceptance run on 24.04 is recorded. This is a stated gap in docs/STATE.md, not a hidden one.
