# ADR-0008: Repository licence — Apache-2.0

Status: accepted · 2026-11-24 · Requirements: LAB-080

## Context

SPEC-001 lets the implementation choose a licence only if the operator specified one. On 2026-11-24 the operator pointed to the project's intended long-term licence, recorded in the core repository's `docs/strategy/cncf-readiness.md` §3 ("License Decision Gate"): Ono-Sendai began under MIT; Apache License 2.0 is the intended CNCF-aligned long-term licence; the core transition is tied to the Kubernetes Cloud-Native Validation Gate or earlier substantive external contributions. §3.4 records the precedent that `ono-sendai-kubernetes`, created without code inherited from the MIT core, started directly under Apache-2.0.

## Decision

`ono-sendai-lab` is licensed under the Apache License 2.0 from its start. `LICENSE` carries the unmodified licence text (identical to `ono-sendai-kubernetes/LICENSE`, SHA-256 `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`); `pyproject.toml` declares `license = "Apache-2.0"`.

The repository contains no code from the MIT-licensed core, so this is an initial licence choice and no relicensing. It neither executes nor pre-empts the core's own transition.

## Consequences

- Until the core transition happens, the project spans two licences: core MIT; Kubernetes provider and lab Apache-2.0. Statements about "the project's licence" name the repository.
- The pinned AWS Emulator is downloaded by each installation and never redistributed here (upstream: CC0-1.0, `vendor/` Apache-2.0). Python dependencies are installed from PyPI, not vendored.
