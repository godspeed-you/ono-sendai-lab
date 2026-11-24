# ADR-0005: Backend boundary and future VM deployment (design only)

Status: accepted · 2026-11-24 · Requirements: LAB-003, LAB-020, LAB-021, LAB-022, LAB-083

## Backend contract

`src/ono_sendai_lab/backend.py` defines the transport-neutral contract; `docs/contracts/backend-contract.json` is its machine-readable form. The lifecycle owns identity, generations, leases, fixture state, single-slot admission and reconciliation. A backend owns *where* a generation runs: `preflight`, `prepare`, `start`, `observe`, `stop`, `call`, `inspect`, `cleanup`, `endpoint_context`. Everything a backend returns about a generation is an opaque `runtime` mapping; the lifecycle stores it and hands it back, without interpreting unit names, paths, PIDs, ports or CA files.

Only `local` exists. `--backend remote-vm` fails with `unsupported_backend` (exit 4) and points here; any other name fails the same way. No stub pretends to work.

## Portable and backend-specific state

| Portable | Backend-specific, never copied |
| --- | --- |
| sandbox names and ids, generation ids/numbers, lease and lifetime deadlines, fixture state, scenario manifests, logical-to-actual mapping (per generation), JSON schemas, dependency pins, qualification tests | host paths, unit names and InvocationIDs, boot ids, control groups, PIDs, ports, CA files, emulator keys, worker socket paths |

## Future VM runbook (not implemented)

1. **Topology.** A dedicated Proxmox VM runs the backend, the emulator and the state store; leases belong to the VM. Development machines run only the client and control plane.
2. **Transport.** A future authenticated private transport (for example SSH over WireGuard) carries the same logical operations. It requires verified host identity, per-user authorization, sandbox ownership per user, and per-owner and aggregate quotas before more than one user may connect. The emulator never gets a public listener and credentials are never exported globally.
3. **Provider end-to-end tests.** Ono/provider runs against the emulator need a guarded test-client execution or network path that preserves TLS and SigV4 and denies real AWS access; a future specification must define it. A green v0.1.0 gate establishes nothing about that integration.
4. **Migration procedure.** Stop all local generations (`aws-lab destroy NAME`), install the same qualified release on the VM with `scripts/bootstrap.sh`, run `aws-lab doctor` and the acceptance suite there, then recreate scenarios with `aws-lab create` / `seed`. There is no live memory migration and no persisted AWS resource: emulated state is disposable by design.
5. **Concurrency.** The VM may run more than one generation only after a measured resource and security qualification and a new specification; v0.1.0 stays at one active generation.

## Trust model today

The local trust model is one trusted operator Unix user running cooperating agents (LAB-022). Separate sandbox state and keys prevent accidental cross-run interference. They do not isolate users from each other and are no defence against a malicious process of the same UID; a separate user or VM is the stronger boundary.
