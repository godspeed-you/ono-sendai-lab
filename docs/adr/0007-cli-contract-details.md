# ADR-0007: CLI contract details

Status: accepted · 2026-11-24 · Requirements: LAB-050, LAB-051, LAB-052

## Optional flags beyond the normative forms

| Flag | Commands | Meaning |
| --- | --- | --- |
| `--json` | all (anywhere before `--`) | exactly one JSON document on stdout |
| `--version`, `--help` | global / per command | no emulator needed |
| `--startup-timeout S` | create, reset | 1..60 s, default 30 |
| `--backend NAME` | create | only `local`; others fail with `unsupported_backend` |
| `--generation ID` | credentials, seed, extend, aws | bind the call to one generation; another current generation yields `stale_generation` |

## Envelope and exit codes

`{"schema_version": 1, "ok": bool, "command": str, "result" | "error"}`; errors carry `code`, `message`, `retryable`, `exit_code`, `details`. The code table is `docs/contracts/error-codes.json`; exit classes: 0 success, 2 usage/validation, 3 not found/conflict/stale/expired/not ready, 4 preflight/security/resource refusal (including `slot_busy`, `disk_budget_exceeded`, `lease_cap_exceeded`, `unsupported_backend`), 5 upstream/transport/timeout/AWS/seed failure, 6 lifecycle/cleanup/internal. AWS error details (`aws_code`, `http_status`, redacted message) sit in `details`; the lab code stays `aws_error`.

`doctor` on an unsafe host returns `ok: false` with `isolation_unavailable`, `resource_control_unavailable` or `preflight_failed` and the full report in `details`.

## `aws` grammar

`aws-lab aws NAME [--generation ID] -- SERVICE OPERATION [--flag VALUE ...]`. List flags take several space-separated values; `--flag=VALUE` is accepted. The machine-readable allowlist with every accepted parameter, kind, filter name and limit is `docs/contracts/aws-operations.json`:

```text
aws-lab aws dev -- sts get-caller-identity
aws-lab aws dev -- s3 list-buckets
aws-lab aws dev -- s3 create-bucket --bucket my-test-bucket
aws-lab aws dev -- s3 delete-bucket --bucket my-test-bucket
aws-lab aws dev -- ec2 describe-vpcs [--vpc-ids vpc-… …] [--filters Name=tag:Name,Values=a,b …]
aws-lab aws dev -- ec2 describe-subnets [--subnet-ids …] [--filters Name=vpc-id,Values=vpc-…]
aws-lab aws dev -- ec2 describe-security-groups [--group-ids …] [--filters Name=group-name,Values=…]
```

Rejected with exit 2: unknown operations, fixture-internal operations, endpoint/profile/region/CA/proxy/credential/output/query/pagination options, `file://`, `fileb://`, `http(s)://` and `s3://` values, values outside per-kind patterns (shell metacharacters never match), repeated flags, more than 20 ids, 5 filters or 10 filter values.

Mutating calls (`create-bucket`, `delete-bucket`) mark an EMPTY or VERIFIED fixture DIRTY before they run; reads leave it unchanged.

## Logs

`logs NAME [--tail N]`: default 50, maximum 200 lines, from the sandbox's ring log (`lab.log`, `lab.log.1`, 256 KiB each, shared by its generations) and the unit's `supervisor.stderr` (only output from before the supervisor redirects its own stderr into the ring log, e.g. a bwrap error). Upstream stdout JSON lines are reduced to timestamp, level, target, message and the endpoint fields before they are written; upstream stderr lines (plain text such as the telemetry notice or a fatal error) are redacted and cut to 300 characters. Every line is redacted again on output; `truncated` reports whether older lines exist.

`--json --help` prints one document with command `help` and the usage text.
