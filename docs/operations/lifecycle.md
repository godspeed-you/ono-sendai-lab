# Lifecycle examples

```sh
aws-lab doctor
aws-lab create --name dev                     # READY, fixture EMPTY, baseline observed
aws-lab status                                # all sandboxes
aws-lab status dev                            # detail: limits, baseline, usage, history
aws-lab seed dev --scenario aws-minimal       # VERIFIED with logical-to-actual mapping
aws-lab aws dev -- ec2 describe-vpcs --filters Name=tag:ono-sendai-lab:scenario,Values=aws-minimal
aws-lab aws dev -- s3 create-bucket --bucket scratch-1   # real mutation; fixture becomes DIRTY
aws-lab reset dev --scenario aws-minimal      # new generation, keys and CA; topology rebuilt and verified
aws-lab reset dev                             # new generation with the empty baseline
aws-lab extend dev --minutes 30               # up to 120 minutes after generation start
aws-lab credentials dev                       # descriptor with references, never secrets
aws-lab logs dev --tail 100
aws-lab destroy dev                           # stops, removes runtime and record; absent names succeed
```

Every command accepts `--json` and then prints exactly one document (schemas in `docs/contracts/`).

## States

Process: `STARTING → READY → STOPPING → STOPPED`, plus `FAILED` (startup failure, emulator death, OOM, interrupted operation, reboot) and `EXPIRED` (lease, absolute lifetime, idle timeout). Fixture: `EMPTY`, `SEEDING`, `VERIFIED`, `DIRTY`, `UNKNOWN`. `seed` needs EMPTY. Any failed or partial seed and any mutation through `aws-lab aws` leaves DIRTY; only `reset` clears it.

Only one generation may be active at a time across all sandboxes. Leases: 30 minutes by default, `extend` up to 120 minutes after generation start, idle stop after 15 minutes without API calls. Enforcement runs inside the generation's systemd unit, so it also works when no CLI is running. An expired or failed sandbox comes back only through `aws-lab reset NAME`.

## Consuming the endpoint

The emulator listens on `127.0.0.1` inside a private network namespace, so the host cannot reach it. Use `aws-lab aws`. `aws-lab credentials` documents the endpoint *in its backend context* plus `managed:` references to the CA, config and credential files inside that context; a future guarded execution path for providers is described in [ADR-0005](../adr/0005-backend-boundary-and-future-vm.md).
