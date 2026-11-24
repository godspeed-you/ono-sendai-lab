# ono-sendai-lab

Disposable AWS API sandboxes for provider development: the official AWS Emulator, pinned and signature-verified, running inside a private network namespace under hard CPU, memory and lifetime limits, driven by one small CLI, `aws-lab`.

## Purpose

Provider work needs real protocol traffic, controllable state and honest failure paths without cloud accounts, spending or credentials. The lab owns the emulator lifecycle and two synthetic fixtures; providers and Ono keep their own semantics. Specification: [SPEC-001](docs/specs/ono_sendai_lab_spec_v0.1.0.md) (v0.1.0).

## Quickstart

```sh
scripts/bootstrap.sh --plan             # preflight, changes nothing
scripts/bootstrap.sh                    # pinned emulator 1.0.598 + hashed venv, no sudo
bin/aws-lab doctor                      # proves containment and limits on this host
bin/aws-lab create --name dev
bin/aws-lab seed dev --scenario aws-minimal
bin/aws-lab aws dev -- ec2 describe-subnets --filters Name=tag:ono-sendai-lab:scenario,Values=aws-minimal
bin/aws-lab reset dev                   # fresh generation, keys and CA, empty baseline
bin/aws-lab destroy dev
```

More: [lifecycle examples](docs/operations/lifecycle.md) · [installation and uninstall](docs/operations/installation.md) · [troubleshooting](docs/operations/troubleshooting.md).

## Prerequisites

Linux x86_64, Python ≥ 3.12, bubblewrap able to create unprivileged user and network namespaces, a systemd user manager on cgroup v2, `gpg`, `dpkg-deb`, about 1.7 GB of disk, HTTPS to `downloads.aws.com` and PyPI during setup. Qualified host: Ubuntu 26.04 LTS ([ADR-0006](docs/adr/0006-host-platform-and-python.md)).

## What you get

- `aws-lab doctor | create | status | credentials | seed | reset | destroy | extend | logs | scenarios | aws` with a versioned `--json` contract ([docs/contracts](docs/contracts)).
- One active generation at a time, capped at one CPU, 1 GiB RAM and no swap; leases of 30 minutes, at most 120, idle stop after 15.
- Fixtures `aws-empty` and `aws-minimal` (bucket, VPC 10.42.0.0/16, subnet 10.42.1.0/24, security group), seeded and verified through AWS APIs.
- Operation-level compatibility evidence for the pinned emulator ([operation-compatibility.json](docs/contracts/operation-compatibility.json)).

## Limitations

- The emulator endpoint lives inside a private namespace; reach it through `aws-lab aws`. Direct Ono or provider end-to-end runs are future work.
- Emulator fidelity is operation-specific, and the emulator is no IAM or security oracle ([security model](docs/operations/security-model.md)).
- Single trusted operator per host; no multi-user or remote backend yet ([ADR-0005](docs/adr/0005-backend-boundary-and-future-vm.md)).

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [ADR-0008](docs/adr/0008-license-apache-2.0.md). Ono-Sendai core is MIT-licensed at the time of writing; Apache-2.0 is the project's intended long-term licence (core `docs/strategy/cncf-readiness.md` §3), and this repository starts there, like `ono-sendai-kubernetes`.

## Project

[AGENTS.md](AGENTS.md) (working agreement) · [CONTRIBUTING.md](CONTRIBUTING.md) · [docs/STATE.md](docs/STATE.md) (progress and evidence) · [ADRs](docs/adr/README.md)
