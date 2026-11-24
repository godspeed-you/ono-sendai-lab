# Installation, preflight and uninstall

## Prerequisites

| Need | Why | Check |
| --- | --- | --- |
| Linux x86_64 | the pinned package is `amd64` | `uname -m` |
| Python ≥ 3.12 with `venv` at `/usr/bin/python3` | host CLI (stdlib only) and private venvs | `python3 -m venv --help` |
| `bwrap` (bubblewrap) able to create unprivileged user + network namespaces | containment (ADR-0002) | `aws-lab doctor` |
| systemd user manager on cgroup v2 with `cpu memory pids` delegated | CPU/memory/swap caps, lifetime | `systemctl --user show-environment` and `cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.subtree_control` |
| `gpg`, `dpkg-deb` | signature check and payload extraction | `command -v gpg dpkg-deb` |
| about 1.7 GB free in your home directory | package 0.22 GB + executable 1.2 GB + venvs | `df -h ~` |
| HTTPS to `downloads.aws.com` and PyPI **during setup only** | the lab never downloads at runtime | — |

No sudo is assumed or used. The lab never changes sysctls, AppArmor, firewall rules, global services, SSH, `~/.aws` or other workspaces.

### Tested host configuration

Ubuntu 26.04.1 LTS, kernel 7.0.0-43-generic, x86_64, systemd 259, distribution bubblewrap with the `bwrap-userns-restrict` AppArmor profile, `kernel.apparmor_restrict_unprivileged_userns=1`, Python 3.14.4, 8 logical CPUs, 64 GB RAM. Ubuntu 24.04 LTS is the spec's primary platform; its CI gate runs on GitHub's `ubuntu-24.04` image and its real acceptance needs a qualified runner (see [ci-runner.md](ci-runner.md) and [ADR-0006](../adr/0006-host-platform-and-python.md)).

## Install

```sh
scripts/bootstrap.sh --plan     # preflight and plan; changes nothing
scripts/bootstrap.sh            # install or re-verify (idempotent)
scripts/bootstrap.sh --dev      # also the hashed gate toolchain (pytest, ruff, jsonschema)
bin/aws-lab doctor              # full preflight incl. a full executable digest
```

Bootstrap verifies the platform against `locks/emulator.lock.json`, measures the disk ledger, downloads the pinned `.deb` and its signature over HTTPS (cached for later offline use), checks the SHA-256, verifies the OpenPGP signature with the pinned AWS publisher key, extracts the payload into a private prefix, checks the executable digest and its `-v` output inside the sandbox, and builds hashed venvs. A repeat run re-verifies and reuses. `--offline` uses only the cached package and signature and refuses if a venv still has to be built (venvs come from PyPI). `--plan` measures and plans and writes nothing.

Locations (all 0700, all counted by the disk ledger):

| Path | Content |
| --- | --- |
| `$XDG_DATA_HOME/ono-sendai-lab/` (default `~/.local/share/…`) | emulator prefix, `venv-runtime`, `venv-dev`, install receipt |
| `$XDG_CACHE_HOME/ono-sendai-lab/` | cached package and signature, gate/acceptance scratch |
| `$XDG_STATE_HOME/ono-sendai-lab/` | sandbox records, bounded logs, lifecycle lock, ledger |
| `$XDG_RUNTIME_DIR/ono-sendai-lab/` | per-generation worker socket and status (tmpfs) |

Put `bin/aws-lab` on your `PATH` if you like (for example a symlink in `~/.local/bin`); the lab does not do it for you.

## Preflight failures

`aws-lab doctor` and every `create`/`reset` refuse to start anything when containment or resource limits cannot be proven, with exit 4 and a remedy. Common causes and fixes are in [troubleshooting.md](troubleshooting.md): AppArmor user-namespace restriction, missing systemd user manager (`loginctl enable-linger` by an administrator on headless hosts), missing cgroup delegation.

## Uninstall

```sh
bin/aws-lab status                    # list sandboxes
bin/aws-lab destroy NAME              # for each one
scripts/bootstrap.sh --uninstall      # removes data, state, cache and runtime locations of the lab
```

Uninstall refuses while sandboxes or lab units exist. It touches nothing outside the four lab locations; the checkout stays.
