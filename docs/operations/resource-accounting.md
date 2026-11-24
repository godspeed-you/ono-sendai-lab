# Resource accounting

## Limits

| Resource | Limit | Mechanism | Verified by |
| --- | --- | --- | --- |
| Active generations | 1 across all sandboxes | lifecycle lock + unit census | A04, A14 |
| CPU | one logical CPU (100 % quota) | `CPUQuota=100%` → `cpu.max 100000 100000` | A04 (kernel file) |
| Memory | 1,073,741,824 bytes | `MemoryMax` → `memory.max` | A04 |
| Swap / zswap | 0 | `MemorySwapMax=0`, `MemoryZSwapMax=0` | A04, A17 (`memory.swap.current = 0`) |
| Tasks | 512 | `TasksMax` → `pids.max` | A04 |
| Lifetime | 120 minutes | supervisor watchdog + `RuntimeMaxSec` | A13 |
| Disk | 5,000,000,000 bytes for the whole run and lab | ledger admission (no hard quota) | A04, gates |
| Logs | 256 KiB × 2 per generation, 200 lines per `logs` call | ring log | A04 |

The CPU and memory caps apply to the generation's cgroup, which holds the emulator plus the in-namespace supervisor/worker. The supervisor's RSS and the host CLI's peak RSS are reported separately (`status NAME` → `resources.processes`, acceptance measurements).

## Measuring

```sh
aws-lab status dev --json | python3 -m json.tool   # cgroup memory.current, memory.peak, cpu usage, emulator/supervisor RSS
aws-lab doctor --json                               # disk ledger: used, headroom, hard_quota=false
```

The ledger sums all four lab locations plus tracked paths registered at bootstrap (`--track PATH`, for example a checkout or run scratch directory), adds a 250 MB reserve and refuses any start, seed, bootstrap or acceptance run that would cross 5,000,000,000 bytes or leave less than 2 GB free on the host. This is conservative admission: there is no hard filesystem quota, so the ledger cannot stop another program from filling the disk.

Measured values for the qualified host are recorded in `docs/evidence/acceptance-evidence.json` and summarised in `docs/STATE.md`; they are observations, not targets.

## Coexistence

Units run with `Nice=10` and best-effort IO priority 7; gates run under `nice -n 10`; tests run sequentially; no stress or load tests. The lab never stops or reconfigures processes outside its own units.
