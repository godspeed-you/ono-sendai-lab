# Pinned upgrade procedure

The emulator version changes only through a reviewed change of `locks/emulator.lock.json`; the runtime never upgrades itself.

1. Pick a version from `https://downloads.aws.com/aws-emulator/packages/aws-emulator/LTS/versions.json` and read the GitHub release notes and fidelity scores for it.
2. Download `aws-emulator_<version>-1_amd64.deb` and its `.sig` into a scratch directory, import `locks/aws-publishing-key.asc` into a throwaway `GNUPGHOME`, confirm the fingerprint `98BC F336 A3BE 53B7 34E9 67C5 2E5B F49B D940 F624` and run `gpg --verify` (expect `VALIDSIG` with that fingerprint).
3. Record in the lock: version, URL, size, package SHA-256, signature SHA-256, executable SHA-256 and size (`dpkg-deb -x` into scratch), the three `-v` output lines, release date, upstream commit, acquisition date. If the publisher key changed, verify the new fingerprint against the AWS documentation and update `locks/aws-publishing-key.asc` and its digest.
4. `scripts/bootstrap.sh` (installs the new version beside the old prefix), then remove the old prefix under `$XDG_DATA_HOME/ono-sendai-lab/emulator/`.
5. Run `scripts/gate.sh` and `scripts/acceptance.sh`. The acceptance run regenerates `docs/contracts/operation-compatibility.json` for the new version; every mandatory operation must stay `verified`. If the startup identity check fails (account or ARN changed), update `qualified_identity` only after confirming the new behaviour, in an ADR.
6. Commit lock, compatibility manifest and evidence together; `scripts/release-check.sh` must pass.

Python dependencies follow the same pattern: update `locks/requirements-*.txt` with hashes for every supported interpreter, keep `pyproject.toml` in step, rerun the gates.
