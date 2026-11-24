# CI runners

`.github/workflows/ci.yml` uses the same scripts as local work, pins every action to a commit SHA, runs with `contents: read` and needs no secrets.

- **gate** (any standard `ubuntu-24.04` runner): `scripts/bootstrap.sh --dev-tools-only` (hashed dev toolchain, no emulator) and `scripts/gate.sh`.
- **acceptance** (separate, visible job): a qualification step checks for bubblewrap, `gpg`, `dpkg-deb`, a reachable systemd user manager and `XDG_RUNTIME_DIR` **without sudo and without changing the runner**. On an unqualified runner the job fails and names the missing pieces; a skipped or missing acceptance run never counts as passed.

## Qualified acceptance runner procedure

A qualified runner is a dedicated, ephemeral Linux x86_64 VM (Ubuntu 24.04 is the spec's primary platform) prepared once by its administrator:

1. `apt-get install bubblewrap gnupg dpkg python3-venv` (keep the distribution's `bwrap-userns-restrict` AppArmor profile).
2. A dedicated unprivileged runner user with `loginctl enable-linger RUNNER_USER`, so `systemctl --user` and `XDG_RUNTIME_DIR=/run/user/UID` exist for the runner service.
3. cgroup v2 with `cpu memory pids` delegated to user managers (Ubuntu default).
4. Outbound HTTPS to `downloads.aws.com`, `pypi.org`, `files.pythonhosted.org` and GitHub; nothing else is required.
5. Register it for this repository only, with a dedicated label, never as a shared privileged runner; point the acceptance job's `runs-on` at that label in a reviewed change.

The developer workstation is not a CI runner. Local runs of `scripts/acceptance.sh` produce local evidence (`"not_ci_evidence": true`) that is never reported as CI evidence.
