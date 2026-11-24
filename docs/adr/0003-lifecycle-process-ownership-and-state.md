# ADR-0003: Lifecycle, generations, process ownership and state schema

Status: accepted · 2026-11-24 · Requirements: LAB-033, LAB-034, LAB-040, LAB-041, LAB-042, LAB-043

## Identity and generations

- Sandbox: random 128-bit hex id plus a unique name `^[A-Za-z0-9][A-Za-z0-9-]{0,47}$`. The leading character is alphanumeric so a name can never be read as an option.
- Generation: random 128-bit hex id plus a counter, new for every start and reset. Keys, CA and port belong to one generation; requests that name another generation fail with `stale_generation` (exit 3).

## State store

`$XDG_STATE_HOME/ono-sendai-lab/sandboxes/<id>/record.json` (0600 in 0700 directories), written atomically (temp file, fsync, rename), read with `O_NOFOLLOW`. One global `flock` serialises every mutating command; read-only `aws` and `logs` take it shared. Records hold no secrets: only references, digests of the CA and a 16-hex fingerprint of the access key id.

State machines are normative and machine-readable in `docs/contracts/lifecycle-states.json`. The single-slot rule counts records in STARTING/READY/STOPPING, records whose runtime has not been verified dead (a failed stop keeps the slot occupied and is retried by every reconciliation; `destroy` fails with `cleanup_failed` until the stop is verified), **and** any active lab unit that no record of this state directory owns. Admission and the start of a generation run under a per-user slot lock in `$XDG_RUNTIME_DIR/ono-sendai-lab/slot.lock`, shared by every state directory of the user, so two registries cannot both pass admission (lock order: registry lock, then slot lock). `reset` performs all validation and admission before it stops the old generation.

## Process ownership

A generation is owned through its systemd unit, never through a PID:

1. Before launch the backend derives the unit name and description (both contain the generation id) and the lifecycle persists them.
2. After launch it records the unit's `InvocationID`, control group and the boot id.
3. Stop = `systemctl --user stop UNIT` only if the recorded InvocationID matches (or, for a start interrupted before the InvocationID was recorded, the generation-specific description matches), followed by verification that the unit is inactive and its cgroup empty. systemd's `KillMode=control-group` reaches only processes inside that cgroup.
4. A record from another boot is reconciled to FAILED (`host-reboot`) without touching anything.

## Leases

- Default lease 30 min, absolute lifetime 120 min from generation start, upstream `--idle-timeout` 15 min.
- The absolute lifetime is enforced twice: by the supervisor watchdog and by systemd `RuntimeMaxSec` (a kernel/service mechanism that works even if the supervisor hangs). `RuntimeMaxSec` cannot change at runtime (verified on systemd 259), so the extendable lease is enforced by the supervisor watchdog, which reads the lease the lifecycle sets over the worker channel. Both run inside the unit and keep working after the CLI exits.
- `extend --minutes N` adds N minutes; beyond the absolute lifetime it fails with `lease_cap_exceeded` (exit 4); once the deadline has passed it fails with `lease_expired` (exit 3), checked by the CLI and again by the supervisor, so an extension racing the watchdog cannot revive a lease.
- The supervisor never polls the emulator; `status` talks only to the supervisor. The upstream health route does not reset the idle timer (observed on 1.0.598).

## Failure semantics

- Startup failure, emulator death, OOM kill, interrupted create/reset, lost runtime → FAILED; lease, absolute lifetime, idle timeout → EXPIRED. The fixture becomes UNKNOWN. Recovery is an explicit `reset`.
- SIGINT/SIGTERM/SIGHUP during create/reset/seed/destroy are turned into an exception so the stop-and-record path runs. A SIGKILLed CLI leaves an operation marker; the next command (holding the lock) stops the prepared unit and records FAILED.
- An unactivated generation stops itself after the startup timeout plus a 30 s grace.

## Test channel

`AWS_LAB_TEST_MODE=1` with `AWS_LAB_TEST_CONFIG` JSON may only shorten minutes (`minute_seconds` 1..60), lower the disk budget or log ring, simulate a missing capability (`bwrap`, `systemd`, `cgroup`), occupy the emulator port (startup failure) or inject one seed fault (`subnet-outside-vpc`, a real upstream error). It also enables read-only probes on the worker channel (connect attempts, path existence, environment names, negative client paths). Production enforcement paths stay the same; nothing in the channel raises a limit or weakens containment. Sandboxes created in test mode are marked `test_mode: true` in `status`.
