# Troubleshooting

Start with `aws-lab doctor` (add `--json` for the full report) and `aws-lab logs NAME`.

## User namespaces

Symptom: `isolation_unavailable`, `bubblewrap could not create the unprivileged namespaces`, or `bwrap: setting up uid map: Permission denied`.

On Ubuntu 23.10 and later `kernel.apparmor_restrict_unprivileged_userns=1` confines unprivileged user namespaces. Ubuntu's `apparmor` package ships `/etc/apparmor.d/bwrap-userns-restrict`, which lets `/usr/bin/bwrap` create them. Check:

```sh
sysctl kernel.apparmor_restrict_unprivileged_userns
ls /etc/apparmor.d/ | grep bwrap
bwrap --unshare-all --unshare-user --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 -- /usr/bin/true && echo ok
```

Remedy (administrator action; the lab never does this): install `bubblewrap` from the distribution and keep the `bwrap-userns-restrict` profile loaded. Disabling the sysctl globally is a weaker host configuration; prefer the profile.

## systemd user manager

Symptom: `resource_control_unavailable`, `cannot query the systemd user manager`, `Failed to connect to bus`.

The lab needs `systemctl --user` with `XDG_RUNTIME_DIR` set. Desktop and SSH logins provide it. Headless hosts or CI need lingering, which an administrator enables with `loginctl enable-linger USER`.

## cgroup delegation

Symptom: doctor reports `cpu.max`/`memory.max` differing from the caps. Check `cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.subtree_control` lists `cpu memory pids`. On cgroup v1 hosts the lab cannot run.

## Lifecycle errors

| Code (exit) | Meaning | Remedy |
| --- | --- | --- |
| `slot_busy` (4) | another generation is active, possibly from another state directory | `aws-lab status`, then `destroy` it; foreign units are listed in `details.units` |
| `name_conflict` (3) | name exists | `aws-lab reset NAME` or `destroy` |
| `startup_failed` (5) | the emulator exited or failed a startup proof; record FAILED | `aws-lab logs NAME`, then `reset` |
| `lease_expired` (3) | lease, absolute lifetime or idle timeout ended the generation | `aws-lab reset NAME` |
| `not_ready` (3) | the generation is not READY (FAILED, STOPPED …) | `aws-lab status NAME` |
| `stale_generation` (3) | a `--generation` reference names an older generation | use the current descriptor |
| `fixture_state_conflict` (3) | seed needs an EMPTY fixture | `aws-lab reset NAME --scenario …` |
| `disk_budget_exceeded` (4) | the 5 GB ledger or the host free-space floor would be crossed | remove lab-owned data or ask the operator |
| `lock_timeout` (3, retryable) | another lifecycle command holds the lock | retry |

## Leftovers

`systemctl --user list-units 'ono-sendai-lab-*'` shows lab units. A unit that no state directory owns blocks the slot; stop it with `systemctl --user stop UNIT` after checking its description names a lab generation.
