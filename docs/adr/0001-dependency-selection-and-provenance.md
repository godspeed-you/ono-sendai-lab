# ADR-0001: Dependency selection and provenance

Status: accepted · 2026-11-24 · Requirements: LAB-010, LAB-011, LAB-012, LAB-013

## Context

SPEC-001 asks for a verified, pinned, prebuilt official AWS Emulator artifact and lightweight Python tooling, with exact provenance and no silent `latest`.

Observed upstream facts (2026-11-24):

- GitHub release `v1.0.598` carries only `scores.json.gz`. The executable is published as Linux packages on `downloads.aws.com` (versioning guide), one `.deb`/`.rpm` per version and architecture, each with a detached OpenPGP signature (`.sig`).
- The documented publisher key fingerprint is `98BC F336 A3BE 53B7 34E9 67C5 2E5B F49B D940 F624` (SSM Publishing).
- The package contains one 1.2 GB static-PIE executable `/opt/aws-emulator/bin/aws-emulator` (depends only on libc), a licence file and two maintainer scripts that only manage a `/usr/bin` symlink.

## Decision

1. **Emulator pin:** `aws-emulator_1.0.598-1_amd64.deb`, recorded in `locks/emulator.lock.json` with URL, size, SHA-256 of package, signature and executable, `-v` output (version, release date, commit `fc05151c…`), qualified identity and acquisition date. The SHA-256 is a locally established pin; the OpenPGP signature is the upstream authenticity proof and is re-verified on every install against the pinned key file `locks/aws-publishing-key.asc` (its digest is locked too).
2. **Install without privileges:** `scripts/bootstrap.sh` downloads over verified HTTPS from `downloads.aws.com` only (redirects elsewhere are refused), verifies digest and signature in a throwaway `GNUPGHOME`, extracts the payload with `dpkg-deb -x` into `$XDG_DATA_HOME/ono-sendai-lab/emulator/<version>/`, removes write bits, runs `aws-emulator -v` inside the sandbox and compares it with the lock. Maintainer scripts never run; no package database, no `/usr/bin` link, no sudo.
3. **Reuse:** an existing install is reused only after the executable digest and the version output match the lock.
4. **Python client:** botocore 1.44.10 (plus jmespath, python-dateutil, six, urllib3) pinned with hashes in `locks/requirements-runtime.txt`; installed with `pip --require-hashes --no-deps --only-binary=:all:` into a private venv. boto3 and the AWS CLI are not needed: botocore already signs SigV4, speaks the query/rest-xml protocols and is a single pure-Python wheel. Development tools (pytest, ruff, jsonschema) are hashed separately in `locks/requirements-dev.txt`.
5. **Host CLI is stdlib-only.** botocore runs only inside the sandbox.

## Consequences

- Reproducibility depends on `downloads.aws.com` keeping versioned packages (documented as stable within a major version). A cached package under `$XDG_CACHE_HOME/ono-sendai-lab/downloads` makes repeat installs offline (`--offline`).
- Upstream publishes security fixes only in the latest release. Upgrading is a deliberate, re-qualified change (docs/operations/upgrade.md).
- The executable is large (1.2 GB); the disk ledger counts it (ADR-0002).
