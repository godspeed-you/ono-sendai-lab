# ADR-0002: Containment, cgroup mechanism and disk accounting

Status: accepted · 2026-11-24 · Requirements: LAB-023, LAB-027, LAB-030, LAB-031, LAB-032, LAB-013, LAB-071

## Containment: bubblewrap

Each generation runs `bwrap --unshare-all --unshare-user --disable-userns --die-with-parent --new-session --cap-drop ALL --clearenv`:

- **Network:** a fresh network namespace whose only interface is `lo`, with no routes. The emulator binds `127.0.0.1` inside it; the host network, host loopback and abstract Unix sockets of the host are unreachable. A connect to the documentation address `192.0.2.1` fails with `ENETUNREACH`.
- **Filesystem:** an empty tmpfs root plus an explicit allowlist: `/usr` (read-only), the emulator prefix, the runtime venv and `src/` (read-only), and two lab-owned writable directories (worker channel, bounded logs). `HOME=/home/lab` is a private tmpfs; emulator keys and CA live there and vanish with the process. `/etc`, `/home`, `/run/user`, the Docker socket, SSH/GPG agents and real AWS files are absent.
- **Privileges:** no capabilities, `no_new_privs`, nested user namespaces disabled, fresh PID/IPC/UTS/cgroup namespaces, no controlling terminal.
- **Environment:** cleared; only `HOME`, `PATH`, `LANG`, `EMULATOR_TELEMETRY=off`, `PYTHONDONTWRITEBYTECODE`, `AWS_EC2_METADATA_DISABLED` are set.

The supervisor re-checks all of this from inside before starting the emulator (`selfcheck.py`) and refuses to run if any check fails.

**Host prerequisite:** unprivileged user namespaces. On Ubuntu 24.04 and later `kernel.apparmor_restrict_unprivileged_userns=1` confines them; Ubuntu ships the `bwrap-userns-restrict` AppArmor profile that lets `/usr/bin/bwrap` create them. The lab changes no sysctl and no AppArmor policy; if bwrap cannot create the namespaces, preflight fails with the remedy in docs/operations/troubleshooting.md.

Alternatives considered: `unshare -rn` (blocked by the Ubuntu AppArmor restriction and cannot assemble a private root), rootless containers (Docker/Podman image builds are excluded by the spec), slirp/pasta networking (adds a route out; the lab needs none).

## Resource controls: transient systemd user units

Each generation is one transient service unit `ono-sendai-lab-<sandbox12>-<generation12>.service` started with `systemd-run --user`:

| Property | Value | Kernel file verified |
| --- | --- | --- |
| `CPUQuota` | 100 % (one logical CPU) | `cpu.max = 100000 100000` |
| `MemoryMax` | 1,073,741,824 bytes | `memory.max` |
| `MemorySwapMax`, `MemoryZSwapMax` | 0 | `memory.swap.max`, `memory.zswap.max` |
| `TasksMax` | 512 | `pids.max` |
| `RuntimeMaxSec` | absolute lifetime (120 min) | unit `Result=timeout` |
| `OOMPolicy` | stop | unit `Result=oom-kill` |
| `Nice` / IO class | 10 / best-effort 7 | — |

After start the lifecycle reads the effective values from `/sys/fs/cgroup/<ControlGroup>/` and refuses READY on any difference. The cap applies to the whole sandbox cgroup (emulator plus the in-namespace supervisor/worker), which bounds the emulator at or below one CPU and 1 GiB. The supervisor's own RSS and the host CLI are measured separately (A17). Requires a systemd user manager on cgroup v2 with delegated `cpu memory pids` controllers (Ubuntu default for logged-in users).

Transient units vanish on reboot and are never enabled, so nothing auto-starts at login.

## Disk accounting

The reference host has no filesystem quota for this user. The lab therefore uses **conservative admission**: before bootstrap, every generation start, every seed and every acceptance run it measures all lab-owned locations (`$XDG_DATA_HOME`, `$XDG_STATE_HOME`, `$XDG_CACHE_HOME`, `$XDG_RUNTIME_DIR` subtrees named `ono-sendai-lab`) plus registered tracked paths (the checkout, run scratch), adds the projected allocation and a 250 MB reserve, and refuses above 5,000,000,000 bytes. It also refuses when host free space would fall below 2 GB. Logs are ring-bounded (256 KiB × 2 per sandbox), test temp directories live inside the lab cache, and pip runs with `--no-cache-dir`. This is measurement plus refusal; there is no hard quota (`doctor` reports `hard_quota: false`).

## Consequences

- Same-UID processes can still read lab files on the host side (see docs/operations/security-model.md). The namespace protects the host from the emulator; it does not protect the lab from the operator's own user.
- The `bwrap` and `systemd --user` prerequisites are typical for desktop Ubuntu sessions; headless hosts need `loginctl enable-linger` by the administrator.
