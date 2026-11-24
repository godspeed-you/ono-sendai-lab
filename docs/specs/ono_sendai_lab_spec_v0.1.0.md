# Ono-Sendai Lab — SPEC-001

## AWS Emulator Foundation (v0.1.0)

**Status:** Proposed initial specification · **Date:** 2026-11-24  
**Target repository:** `godspeed-you/ono-sendai-lab`  
**Release target:** `v0.1.0` (implementation only; release promotion separate)  
**Canonical artifact:** this Markdown document; any PDF is a reading copy.

## 0. Reading guide and authority

This specification defines a complete, deliberately small first release of an independent infrastructure test lab. It is not a feature specification for the Ono-Sendai shell. The immediate deployment target is an existing Linux development machine, on which another coding agent may already be building Ono-Sendai. The eventual target is a dedicated Proxmox VM accessible from multiple development machines. The first release must be useful today without requiring that VM.

MUST, MUST NOT, SHOULD and MAY express requirement strength. The numbered requirement IDs are stable identifiers for issue, contract and acceptance traceability. Examples are illustrative unless explicitly marked as normative. Once adopted into the repository, this initial specification MUST remain byte-identical. Resolve ambiguity by ADR; a scope change requires a subsequent specification and explicit approval. Do not edit the adopted document to make an implementation pass.

Authority order: adopted specification → machine-readable contracts → ADRs resolving ambiguity → acceptance definition → tests → implementation. Lower layers must not contradict higher layers. The repository's `AGENTS.md` is the operational working agreement; it cannot override this specification. `docs/STATE.md` records actual progress and evidence, not new requirements.

## 1. Purpose and scope

### 1.1 Problem

Provider development needs reproducible infrastructure with real protocol interactions, controllable state and meaningful negative tests. Real cloud accounts introduce costs, credentials, permissions and destructive-action risk. Mock responses alone do not demonstrate that a client can communicate with a real AWS-compatible endpoint or discover resources created by another client. The AWS Emulator provides a useful intermediate layer, but its fidelity is operation-specific and it is not an AWS security oracle.

The lab is the owner of the emulator lifecycle and synthetic fixtures. A provider is the owner of provider semantics. Ono is the owner of its shell and capability semantics. Neither the lab nor a fixture must implement those semantics on behalf of the provider.

### 1.2 Goals

- **LAB-001:** A separately versioned repository provides a repeatable Linux installation, a small CLI and isolated, disposable AWS environments with a documented contract.
- **LAB-002:** A coding agent can create, inspect, populate, reset and destroy a local sandbox without AWS account access, cloud spending or a build of the emulator or Ono.
- **LAB-003:** The same lifecycle and scenario contracts are transport-neutral. Deployment-specific implementation details remain behind a backend boundary; the next deployment can use a dedicated VM without redefining the scenarios.
- **LAB-004:** Real emulator integration and failure-path evidence, explicit resource accounting and a reproducible quality gate are delivered; this is not a scaffold or a proof of concept.

### 1.3 Non-goals (v0.1.0)

- No changes to Ono-Sendai, KUANG/11, existing provider repositories, their branches, issues or running builds. No Ono compilation, Cargo invocation, embedded emulator, Docker or Kubernetes deployment.
- No real AWS requests, AWS accounts, IAM/security-conformance claims, cloud credentials, third-party SaaS or cloud provisioning. No production workload, Lambda execution, emulated data-plane listeners, multi-account or cross-region simulation.
- No Proxmox provisioning, SSH service, public endpoint, network-facing API, authentication server, multi-user service or production-ready remote backend. No arbitrary third-party command execution inside a sandbox in this release.
- No load/scale benchmark, enterprise topology, GUI, web console, generative AI service, GCP/Azure backend or packaged integration into Ono release gates.

These exclusions are intentional resource and security boundaries, not missing v0.1.0 deliverables. A later release may add them through explicit specifications.

## 2. Upstream facts and qualification

The selected upstream is the official AWS Emulator, not LocalStack, SAM or the Lambda Runtime Interface Emulator. AWS documents an HTTPS API on loopback by default, SigV4 authentication, per-run generated credentials and CA, a health route, in-memory state and support for multiple processes on different ports. Reset requires restart. The documented startup artifacts reside under a port-specific directory in the emulator's home. Authentication and TLS are distinct controls and both are required here.

AWS documents limited policy fidelity, incomplete account and regional isolation, and warns against using the emulator to prove cloud security. It also does not position the tool as a long-running production or load-testing service. Some AWS resources expose extra local ports; they are out of scope. The lab MUST NOT infer compatibility for an operation merely from an upstream service-wide score or an accepted HTTP response.

### 2.1 Sources (checked 2026-11-24)

- [Upstream project](https://github.com/aws/aws-emulator) and [release assets](https://github.com/aws/aws-emulator/releases).
- [Linux installation](https://docs.aws.amazon.com/emulator/latest/userguide/getting-started-linux.html) and [CLI reference](https://docs.aws.amazon.com/emulator/latest/userguide/cli-reference.html).
- [SDK setup](https://docs.aws.amazon.com/emulator/latest/userguide/point-sdk-to-emulator.html) and [limitations](https://docs.aws.amazon.com/emulator/latest/userguide/).
- [Security](https://docs.aws.amazon.com/emulator/latest/userguide/security.html), [fidelity](https://docs.aws.amazon.com/emulator/latest/userguide/fidelity.html) and [versioning](https://docs.aws.amazon.com/emulator/latest/userguide/versioning.html).
- [Upstream operation checks](https://docs.aws.amazon.com/emulator/latest/userguide/check-project.html) and [evaluation suite](https://github.com/aws/aws-emulator-evaluation-suite).

The release listing observed during preparation contained `v1.0.598`. This is a qualification candidate, not a validated binary or immutable dependency decision. Select an exact supported asset during implementation; record it before use. Upstream updates frequently; documentation and fidelity observations must be tied to the tested version.

### 2.2 Dependency and installation contract

**LAB-010:** Ubuntu 24.04 LTS on Linux x86_64 is the primary acceptance platform. Python 3.12 is the reference runtime. Other Linux distributions and architectures are not promised; report them as unqualified unless tested. Prefer Python standard library; use a small, pinned AWS SDK dependency (e.g. boto3/botocore) or a pinned existing AWS CLI. ADR documents the choice and the reproducibility trade-off. No runtime dependency installation, no Rust compilation, no giant SDK source checkout.

**LAB-011:** Provide an explicit install/bootstrap command with preflight, a dry run or plan, idempotency, verified platform/architecture, dependency lock, predictable locations and an uninstall path. Use a prebuilt upstream package or safely extracted prebuilt executable. Prefer a private install prefix to avoid replacing a system-wide package. Downloads and dependency installation occur only in the setup phase; runtime never downloads or upgrades anything. Do not execute upstream installation scripts blindly.

**LAB-012:** Record exact release, asset URL, SHA-256, architecture, version output, acquisition date and checksum provenance in a versioned lock. Verify publisher-provided checksums/signatures where available; otherwise explicitly record that the checksum is a locally established pin, not an upstream authenticity proof. Obtain only from authoritative release channels via verified HTTPS. Reject missing/mismatched digest and architecture; never silently use `latest`. Pin Python dependencies and transitive dependencies if any, with hashes where supported. A version already present on the host is reusable only after verifying identity and compatibility.

**LAB-013:** No privilege escalation, package installation with elevated privileges, firewall/network changes, system-wide service changes, changes to SSH, global environment or other workspaces without explicit operator approval. If a required isolation feature is unavailable, preflight fails with a precise remedy and the lab remains non-running. Do not weaken an isolation control to make installation pass.

## 3. Architecture and trust boundaries

### 3.1 Logical components

```text
Caller / coding agent
        |
        v
aws-lab CLI (host-side control plane)
        |
        v
Lifecycle manager + state store + backend contract
        |
        v
Local backend (v0.1.0) / future remote backend
        |
        v
Isolated runtime: emulator + managed AWS API client
        |
        v
AWS-compatible HTTPS endpoint on isolated loopback
```

**LAB-020:** Use a small Python package and executable CLI. Keep lifecycle, scenario definitions, upstream process adapter, isolation mechanism, AWS client adapter and local persistence separate. Do not create a general-purpose orchestration framework. The backend contract must cover preflight, start, health, stop, inspect, guarded client operations, and cleanup; the lifecycle layer owns leases and generation semantics. Scenario code uses the managed AWS client interface, never subprocess shell strings or host-specific network details.

**LAB-021:** Only the local backend is implemented and supported in this release. A documented transport-neutral contract distinguishes logical sandbox ID, generation, backend, endpoint context and lease. The control plane must not assume that its own filesystem paths, PID, or loopback address are valid on a future remote host. No remote stubs pretending to work; unsupported backend selections fail explicitly. A future remote adapter is expected to transport the same logical operations, not to expose the emulator directly to the internet.

**LAB-022:** The local trust model is a single trusted operator/Unix user running cooperating agents. Separate sandbox state and keys prevent accidental cross-run interference, not malicious same-UID access. Do not claim OS user isolation or adversarial-agent containment. Other users and arbitrary host processes are not given access. The future multi-user VM needs its own authentication, authorization, namespace/identity and quotas design before being enabled.

### 3.2 Containment and managed client

**LAB-023:** Runtime must have independently enforceable network containment. The emulator and managed client run in a dedicated network namespace (or demonstrably equivalent OS-level isolation) with loopback only, no default external route, no host network access and no inherited proxy or external network sockets. An ordinary endpoint override, environment variables, IAM simulation or a DNS blacklist is insufficient. Select the smallest workable unprivileged mechanism (e.g. a rootless namespace worker) and document its host prerequisites in an ADR. An approved administrator-provisioned equivalent is allowed only with explicit approval. If containment cannot be proven, do not run the emulator or managed AWS operations. Do not alter global host network settings to satisfy this.

**LAB-024:** Bind explicitly to `127.0.0.1` in the isolated runtime. Keep upstream authentication enabled and telemetry disabled. Verify effective configuration; all client requests use verified HTTPS with the current run CA and SigV4. Never use `--no-verify-ssl`, unverified requests or `--auth off`. A verified health response is necessary but not sufficient: a signed STS identity probe must demonstrate the emulated account and intended region context before state is READY. If upstream identity behavior changes, record a qualified version or fail the gate; do not silently assume it is safe.

**LAB-025:** The managed client constructs a fresh allowlisted environment and an explicit SDK session from current emulator-generated credentials. It never consults the operator's `~/.aws`, ambient AWS variables, SSO, credential processes, web identity, instance/container metadata, system proxies, shared global profiles or real AWS endpoints. Use an isolated HOME and explicit config/credential/CA paths; disable metadata discovery and redirects to external hosts. Use the same verified endpoint and region for every client. Prefer `us-west-2` for regional services and the same logical account throughout; verify STS/S3 behavior on the pinned binary. Any global-service exception must stay inside the same endpoint and be explicit in the compatibility manifest. Never fall back on the default SDK endpoint, including after a timeout or reset.

**LAB-026:** A local sandbox endpoint may be unreachable from the host because it is in an isolated namespace. This is expected. `aws-lab aws` and fixtures run through a managed worker inside that context. The initial lab is useful for qualifying the AWS API and fixtures; direct Ono execution inside the sandbox is a subsequent integration task, not a hidden host-network bypass. The worker/control channel may use a private local Unix socket or equivalent IPC; validate requests, bind to the current owner and generation, limit message size and never expose an unauthenticated TCP manager. Do not add arbitrary shell execution, port forwarding, or a host-exposed raw API in v0.1.0.

**LAB-027:** The emulator is untrusted upstream software relative to the development host. Run non-root, with minimal privileges and no access to real credentials, unrelated home/workspace files or sensitive sockets; use a private runtime directory, explicit file mounts/allowlist or equivalent filesystem isolation. No host Docker socket, SSH agent, GPG agent, cloud token, writable system paths or external code execution features. A privileged setup helper must not become an unrestricted agent tool. A separate user/VM is the stronger boundary for future workloads; record residual same-user risks without claiming a security certification.

## 4. Resource and coexistence requirements

**LAB-030:** First-round host budget: at most one active emulator generation at a time (including STARTING and STOPPING); emulator CPU capped at one logical CPU (100% quota), RAM at 1 GiB (1,073,741,824 bytes), no emulator swap where supported. Apply hard limits with delegated cgroup v2/systemd or an equivalent kernel-enforced mechanism, verify effective values and fail closed if unavailable. Never kill or reconfigure another user's process to obtain resources. A small manager/client may use additional transient resources; measure separately and report them. Do not run large parallel test workers.

**LAB-031:** Maximum 5 GB (5,000,000,000 bytes) *additional aggregate disk usage* attributable to the entire implementation run and lab, not merely the checkout: package downloads, extracted binary, dependencies, repo/Git objects, virtualenv, caches, state, logs, test evidence, build/install temp files and all lab-managed locations. Inventory a baseline before downloading, verify available headroom, bound log/output/temp sizes, avoid duplicative downloads, check before allocations and periodically during gates. Use filesystem quota or bounded dedicated storage where available; otherwise implement conservative admission and measurement with a safety margin, document its limitations and abort before exhausting budget. Do not claim a hard filesystem quota if none exists. Never delete unrelated files or prune system-wide caches. On resource uncertainty or pressure, stop lab-owned workloads safely and report; no budget extension without operator approval.

**LAB-032:** Setup and tests must coexist with the ongoing Ono v0.6.6 implementation. No Cargo/rustc, emulator source build, Docker image build, cluster, host-wide benchmark or concurrent stress tests. Check host disk and memory pressure before substantial work, avoid saturating I/O, use low-priority noncritical tasks where practical, and do not interrupt other jobs. Measure idle and normal fixture CPU/RSS/cgroup peak/disk deltas and gate wall time; these are evidence, not predetermined success values. No real-cloud or remote-machine dependency.

**LAB-033:** Runtime resource exhaustion, OOM, timeout and partial install must be reported truthfully. A killed emulator becomes FAILED/EXPIRED as appropriate; its generation is invalid, and no apparent healthy sandbox may be returned. Recover via explicit new generation; do not auto-restart during an active test and hide loss of state.

**LAB-034:** Default lease 30 minutes, maximum absolute lifetime 120 minutes from generation start; default upstream idle timeout 15 minutes where supported (never a substitute for the hard lease). `extend` may move the deadline up to the maximum, not beyond it. A supervisor/watchdog or kernel/service lifetime mechanism must enforce the absolute deadline even if the CLI exits. Health polling must not keep an idle instance alive indefinitely; distinguish idle from hard expiry. Test fixtures and all gates destroy their own instances on completion and on ordinary failure/interruption; watchdog covers abrupt loss. Do not auto-start a lab at login or reboot.

## 5. Lifecycle, identity and persistence

**LAB-040:** Sandbox identity is a stable opaque ID and a unique user-visible name; constrain names to ASCII letters/digits plus hyphen, 1–48 characters, no path traversal. An immutable generation ID changes for every successful start/reset. Operations resolve a single owned sandbox under a lock and reject stale generation references. Store state atomically in an owner-private location (XDG directories or documented equivalent). No plaintext secrets in the lifecycle registry. Concurrent create/reset/destroy cannot create extra runtimes or stop an unrelated process.

**LAB-041:** State model (normative): `STARTING`, `READY`, `STOPPING`, `STOPPED`, `FAILED`, `EXPIRED`. `create` reserves a slot, provisions runtime, establishes limits and verifies health/identity before READY; failures clean resources and release the slot only once death is verified. `reset` performs verified stop of the previous generation and starts a new one under the same sandbox ID/name; never two active generations. `destroy` stops and removes lab-owned runtime and metadata (idempotent for absent name); `status` detects discrepancies and reports failure/expiry, not stale READY. No PID-only ownership: use supervisor identity, process start identity or equivalent robust ownership evidence. On host reboot reconcile stale records; do not signal recycled PIDs.

**LAB-042:** All emulated AWS resource state is disposable. Stop/reset destroys in-memory resources. Reset rotates keys/CA and invalidates old client contexts; no snapshot/restore promise. By default reset yields the empty baseline; `reset --scenario NAME` optionally seeds that baseline and reports success only after verification. Fixture verification is not the same as merely having an active emulator. A failed/partial seed is recorded as DIRTY, never reported as a verified fixture. `seed` only accepts an EMPTY fixture state; repeated seed refuses without mutation and instructs reset. Keep fixture state (`EMPTY`, `SEEDING`, `VERIFIED`, `DIRTY`, `UNKNOWN`) separate from process state. User-initiated mutation outside a seed invalidates VERIFIED, even if it appears harmless.

**LAB-043:** Startup waits with bounded timeout (default 30 s; documented configurable maximum 60 s), checks the documented TLS health endpoint, validates signed identity and validates runtime isolation. CLI calls have bounded connect/read timeouts and bounded retries, no blind retries of ambiguous mutations. Stop requests graceful termination then bounded escalation of only owned processes; report cleanup failure if still active. Never reuse stale credentials, ports or CA files; private runtime files are removed on stop/destroy after process death. Cleanup and reconciliation work after crash, failure, signal or forced termination.

## 6. Public CLI contract

**LAB-050:** Executable name `aws-lab`. All commands are noninteractive and support machine-readable results via a documented global `--json` option. `--help` and `--version` work without the emulator. Human output is concise, JSON stdout contains exactly one document, stderr carries redacted diagnostics. No shell evaluation, unsafe interpolation or implicit current sandbox. All examples below are normative invocation forms; optional flags beyond these are ADR-governed.

```text
aws-lab doctor
aws-lab create --name NAME
aws-lab status [NAME]
aws-lab credentials NAME
aws-lab seed NAME --scenario SCENARIO
aws-lab reset NAME [--scenario SCENARIO]
aws-lab destroy NAME
aws-lab extend NAME --minutes N
aws-lab logs NAME [--tail N]
aws-lab scenarios
aws-lab aws NAME -- SERVICE OPERATION [ARGS...]
```

- `doctor`: versions, platform, resource controls, network/file isolation, dependency availability and safe preflight. Does not create AWS resources or make cloud calls. Reports reasons for an unsafe/unavailable backend.
- `create`: new sandbox, empty fixture baseline and current generation descriptor; duplicate name is conflict. A live sandbox may be explicitly reset or destroyed instead.
- `status`: list owned sandboxes or show one; include state, fixture status, generation, backend, timestamps, deadline, region and diagnostics. No credentials or sensitive host details.
- `credentials`: returns a connection descriptor containing generation, region, endpoint *in its backend context*, authentication mode, CA reference and opaque credential/config references. No raw secret on stdout/stderr, no shell-export instructions that bypass containment, no copying to global AWS config. Private credential files remain accessible only to the managed context. Document how downstream integration will consume the descriptor; the host is not promised direct endpoint access.
- `seed`, `reset`, `destroy`, `extend`: semantics from sections 4–5. `extend` adds N positive whole minutes to the existing deadline, rejecting values above the cap. No lease revival after expiry. `destroy` absent name succeeds without touching any other state.
- `logs`: bounded redacted tail for lab-owned logs, default 50 lines, maximum 200, with clear truncation indication. No request bodies, signed headers, credentials or arbitrary paths. Never emit raw upstream logs until inspected/sanitized.
- `scenarios`: list available scenario IDs, versions, requirements and qualified/unsupported operations; works without active sandbox.
- `aws`: managed AWS operation runner; accept a documented subset of AWS CLI-like service/operation/argument syntax or explicitly typed JSON parameters. Publish exact supported forms and implement the examples in the acceptance suite. Explicit operation and parameter allowlist; reject caller-provided endpoints, profiles, credential settings, CA overrides, proxy settings, input file URLs, arbitrary output paths and shell syntax. No generic subprocess pass-through. Return AWS result/error with secrets redacted. Calls require READY generation and refuse stale contexts. Read-only operations do not mark fixture dirty; mutations do.

**LAB-051:** Minimum `aws` operation surface: STS `get-caller-identity`; S3 `list-buckets`, `create-bucket`, `delete-bucket`; EC2 `describe-vpcs`, `describe-subnets`, `describe-security-groups`. Fixture-internal allowlist additionally includes EC2 create VPC/subnet/security group and required tagging/read operations; document exact accepted parameters. Do not expose a general AWS SDK dispatcher. Support enough bounded parameters to demonstrate fixture independence and failure behavior. No emulated data-plane service operations. If S3 regional creation parameters require adaptation, encode them explicitly, never by real AWS fallback.

**LAB-052:** JSON contract version `1`, fields `schema_version`, `ok`, `command` and either `result` or `error`; errors have stable `code`, safe message and retryability indication. Result fields and nullable/absent semantics are described in versioned machine-readable schemas. Numeric exit status: `0` success, `2` usage/validation, `3` not found/conflict/stale generation, `4` preflight/security/resource refusal, `5` upstream/transport/timeout/operation failure, `6` lifecycle/cleanup/internal failure. AWS error details may appear in redacted structured form, not replace the lab-level classification. Unknown command or backend fails explicitly. Human messages must not contradict JSON or exit codes.

## 7. Scenario model and operation qualification

**LAB-060:** Scenarios are versioned synthetic input manifests plus executable seed/verify logic. No real account identifiers, dumps, customer data, secrets or random external dependencies. Logical names are deterministic; AWS-assigned IDs may vary by generation. Capture logical-to-actual resource mapping after creation. Verify by independently reading through AWS APIs and asserting identities, counts for lab-owned resources, attributes and relationships. Baseline default AWS resources may exist; never define empty as zero objects of every AWS service. Record baseline and distinguish fixture-owned resources from defaults. Do not fake missing edges or return expected state from the manifest in lieu of observed state.

### 7.1 Required scenarios (complete in v0.1.0)

| ID | Fixture and verification |
| --- | --- |
| `aws-empty` | No lab-created resources. Verify signed identity and an observed baseline for S3 and EC2. Default VPCs or security groups, if any, are not fixture-owned. |
| `aws-minimal` | One synthetic S3 bucket; one tagged VPC (10.42.0.0/16); one subnet inside it (10.42.1.0/24); one named/tagged security group in that VPC. Verify each resource via AWS APIs and VPC references for subnet and group. Use a generation-scoped valid bucket name, deterministic logical labels and no real network or compute workload. |

The `aws-minimal` fixture demonstrates genuine cross-object discovery using API metadata, not actual network reachability. No EC2 VM, EBS attachment, ALB, RDS, Lambda, IAM-policy scenario or large resource population is required in v0.1.0. `aws-degraded`, `aws-enterprise` and `aws-scale` are future scenario proposals, not supported IDs. Negative testing in this release uses controlled errors, process loss and invalid requests, not a fabricated degraded topology.

**LAB-061:** Seed is transactional only at the lifecycle level: a partially successful AWS sequence can leave resources. On failure mark DIRTY, report the failed operation and safe partial evidence, and require reset. No claims of atomic AWS rollback. Verify with bounded polling where an API has asynchronous state; no unbounded sleeps or retries. `reset --scenario aws-minimal` recreates and verifies the logical topology with fresh generation and credentials; do not demand identical generated IDs. Baseline/fixture evidence is small, redacted and reproducible.

**LAB-062:** Maintain a machine-readable operation compatibility manifest: scenario/operation, exact emulator and client version, tested region, upstream fidelity reference if available, observed outcome, evidence/test reference, known limitation and qualification status. Mark only directly tested operations as verified; service-wide fidelity percentages do not qualify untested operations. Mandatory operations that fail on the chosen release block the gate; investigate a compatible pinned upstream release or report the blocker. Do not silently substitute mocks, drop fixture resources, redefine assertions or call real AWS to resolve discrepancies. Unsupported optional operations are documented and not counted as passed.

## 8. Security and privacy acceptance

**LAB-070:** Store state/config/key/CA files in private owner-only directories (0700) and secrets (0600), with safe creation, no symlink/path traversal, no secrets in argv, Git, issues, console, fixtures or evidence. Redact access keys, secrets, tokens, signed URLs and authorization headers, including crash paths. Reject unsafe configuration and keep telemetry off. Use only synthetic resource names/data; no reading or inspecting actual AWS config files even for a safety check.

**LAB-071:** Negative tests independently demonstrate that a managed runtime cannot reach a harmless controlled listener outside its network namespace (including a host-loopback listener), has no external route or inherited proxy, and cannot use poisoned ambient credential/config variables or override options. Test with synthetic sentinel keys/files only; no real AWS address is contacted to demonstrate blocking. Verify wrong emulator credentials, wrong CA, stale generation and unreachable endpoint fail without fallback. Test that actual credential files and host-sensitive paths are not mounted into the runtime using synthetic sentinels in a controlled directory. Tests must not log secrets. These tests qualify the lab's controls, not IAM fidelity or general host security.

**LAB-072:** Provide a threat model that distinguishes accidental misuse, compromised upstream process, same-UID host attacker, future multi-user remote attacker and actual cloud authorization. Explain what is prevented by kernel isolation versus CLI validation, what is unproven, and why emulator success is not evidence for production security. No adversarial agent security claim; no claim of absolute protection from kernel exploits. Report any failed/untested security control as a blocker, not a warning hidden from release checks.

## 9. Repository and documentation

**LAB-080:** Keep repository layout small and explicit. The following paths are required; source/test substructure is the implementer's decision. Use existing repo conventions if compatible, without renaming the adopted spec.

```text
README.md
AGENTS.md
CONTRIBUTING.md
pyproject.toml
docs/STATE.md
docs/specs/ono_sendai_lab_spec_v0.1.0.md
docs/adr/
docs/contracts/
docs/operations/
scenarios/
scripts/bootstrap.sh
scripts/gate.sh
scripts/acceptance.sh
scripts/release-check.sh
tests/
.github/workflows/
```

`README.md` is a concise landing page with purpose, quickstart, prerequisites, limitations and links. Operations documentation provides installation, preflight failure remedies (including Ubuntu namespace restrictions), clean uninstall, lifecycle examples, troubleshooting, resource accounting, pinned upgrade procedure and security model. Document the exact tested host configuration, no assumed sudo privileges, and the local-to-VM boundary. `AGENTS.md` records safety constraints and continuation protocol. `docs/STATE.md` includes current scope, commit, completed/pending gates, found-not-yet-filed findings, evidence links and safe continuation instructions. Keep version and public contract metadata consistent. Choose a project license only if the operator has specified one or the repository already has one; otherwise record licensing as an unresolved publication prerequisite, not a guessed license.

**LAB-081:** ADRs record dependency selection/provenance, containment and cgroup mechanism, lifecycle/process ownership, client adapter, state and generation schema, and backend/remote transport boundary. Combine ADRs when coherent. Machine-readable contracts cover CLI envelope, lifecycle/fixture states, scenario manifest, operation compatibility and dependency pin. Maintain a spec hash and requirement-to-test index. Do not commit runtime files, private paths, downloaded binaries, venvs or large raw logs. Provide `.gitignore` and secrets checks.

**LAB-082:** GitHub issues, when available, represent actionable scoped work. Inspect and deduplicate before creating issues; use target milestone `v0.1.0` and meaningful labels consistent with this repository. Include requirement IDs, dependencies, scope and acceptance evidence. Close only verified issues, with commit/test reference. Do not mark partially tested work complete. If tracker access is unavailable, record equivalent local work items and report the limitation. No edits to other repositories. Work on `implementation`; no merge, tag, package publication or release without explicit promotion.

### 9.1 Future VM migration contract (design only)

**LAB-083:** Provide a deployment decision record/runbook with the following boundaries:

- The future VM runs the backend and emulator; a development machine runs only the client/control plane. State and leases belong to the backend host.
- Version pins, scenario definitions, logical IDs, JSON schemas and qualification tests are portable. Host paths, PIDs, CAs, keys and ports are generation/backend-specific and must not be copied as persistent resources.
- A future authenticated SSH or other private control transport may carry operations over WireGuard; verified host identity, per-user authorization, sandbox ownership and per-owner/aggregate quotas are required before multi-user operation. No public emulator listener or global credential export.
- To support actual Ono/provider end-to-end tests, a future specification must provide a guarded test-client execution or network path that preserves TLS/SigV4 and denies real AWS access. A successful lab v0.1.0 gate does not establish this integration.
- Migrate by stopping local generations, installing the same qualified release on the VM, and recreating scenarios. No live memory migration or persisted AWS resources. A future backend may increase concurrency only after measured resource and security qualification; the first release remains one active generation.

Do not implement speculative remote orchestration. The deliverable is an exercised local backend plus a documented, reviewable extension boundary, not a remote PoC.

## 10. Testing and release gates

**LAB-090:** Use deterministic unit and contract tests for validation, state transitions, leases, process identity, resource admission, errors, CLI schemas, secret redaction, scenario mapping and failure recovery. Fakes are allowed at external boundaries in unit tests, never as a replacement for required real emulator, OS isolation or cgroup acceptance. Add regression tests before fixes where practical. Do not weaken assertions, skip required tests, mark expected failure to conceal a defect or return hard-coded acceptance outputs.

**LAB-091:** `scripts/gate.sh` runs syntax/format/lint checks, unit/contract tests, spec-hash/contract/traceability checks and secret scanning. `scripts/acceptance.sh` runs required real local emulator and OS-control tests sequentially in fresh lab-owned state. `scripts/release-check.sh` runs both against the final checkout, checks docs and generated evidence, verifies cleanup and prints `release-check: ono-sendai-lab is release-ready` only on full success. Exit nonzero on missing dependency, failed isolation, budget violation, missing mandatory evidence or failed assertion. All scripts must be repeatable, noninteractive, time-bounded, safe on interruption, and avoid installing dependencies silently. Do not treat a command that could not run as passed.

**LAB-092:** CI uses the same scripts, pinned actions/dependencies and least privilege. Unit checks may run independently on a standard runner. Required real acceptance is a separate visible job on a qualified Linux runner with isolation and limits. Do not run CI jobs on the user's already busy development host unless explicitly authorized; do not introduce a privileged shared runner or change its configuration. If hosted CI cannot enforce required controls, record the external blocker and provide a qualified local/VM runner procedure. A local pass cannot be called a CI pass; do not publish a release until required CI evidence is available. CI must never require AWS secrets, deploy real AWS resources or build the emulator. Tests and logs must avoid leaking generated secrets.

### 10.1 Mandatory acceptance matrix

Every row has an executable test, stable ID and evidence reference. A row cannot pass on documentation alone unless explicitly a documentation review.

| ID | Acceptance condition |
| --- | --- |
| A01 | Bootstrap validates platform, locked artifact and digest; repeat install is safe; corrupt digest rejected. |
| A02 | Doctor detects missing namespace/cgroup support; unsafe preflight refuses startup without side effects. |
| A03 | Real emulator starts from prebuilt binary with auth on, telemetry off, verified TLS health and signed STS identity. |
| A04 | Effective CPU and memory caps verified; second concurrent create refuses; disk admission and bounded logs exercised. |
| A05 | `aws-empty` observes baseline without creating lab resources; status and JSON contract are correct. |
| A06 | `aws-minimal` creates and independently verifies all four fixture resources and both VPC relations. |
| A07 | Direct managed client operations observe fixture data; separate bucket create/read/delete demonstrates real mutation. |
| A08 | Duplicate seed refuses without mutation; partial seed is DIRTY; reset with scenario reconstructs verified topology. |
| A09 | Reset changes generation/key/CA, invalidates old descriptor, removes old resources and yields empty baseline on plain reset. |
| A10 | Wrong credentials, wrong CA, unreachable endpoint, invalid arguments and stale context fail with no cloud fallback. |
| A11 | Namespace cannot reach controlled host/external-namespace listener; ambient credential/proxy poisoning is ineffective; filesystem sentinels protected. |
| A12 | Unexpected emulator death and startup failure are recognized; cleanup removes owned state, never signals unrelated process. |
| A13 | Lease expiry enforced after CLI exits; extension cap, idle timeout and no revival tested using bounded test durations. |
| A14 | Concurrent lifecycle requests, port collision and interrupted create/reset cannot leave two live generations or stale READY. |
| A15 | Destroy is idempotent; after success/failure/interrupt gates no lab-owned emulator/worker or credentials remain active. |
| A16 | JSON schemas/exit codes, help/version, logs redaction and path/parameter validation match contract. |
| A17 | Actual idle/normal resource measurements, version-specific operation coverage, spec hash and test evidence recorded. |
| A18 | Documentation review: fresh install/usage/uninstall, host prerequisites, security limitations and future VM migration are actionable and consistent. |
| A19 | CI workflow configuration validated; actual CI result or precise runner blocker reported separately. |

Tests for timeout/expiry may inject short durations through a test-only configuration while proving the same production enforcement path. Do not wait 120 minutes or allocate 1 GiB/5 GB merely to exercise the limits; inspect effective kernel settings and test boundary/refusal logic with small controlled budgets. No host-wide load tests. Run at least one complete create → seed → inspect → reset → inspect → destroy lifecycle against the real emulator after the last relevant code change.

## 11. Implementation sequencing and completion

The following phases are execution guidance, not an imposed internal design. Each phase ends with a recorded verification result; preserve a safe continuation state between them.

1. **P0 — Discovery and admission:** inspect repository, current host pressure, upstream documentation/assets, resource/namespace support; pin the intended dependency and record decisions. Stop before installation if approval or hard isolation prerequisites are missing.
2. **P1 — Foundation:** CLI/JSON contract, resource ledger, lifecycle state and tests; safe bootstrap/uninstall; pinned dependency acquisition. Prove limits and containment before running the emulator.
3. **P2 — Real backend:** actual start/health/identity/client/stop/reset, process ownership, TTL, concurrency, cleanup, negative isolation tests.
4. **P3 — Fixtures:** `aws-empty` and `aws-minimal`, operation compatibility manifest, real API evidence, partial seed and reset semantics.
5. **P4 — Hardening:** independent code/security/acceptance review, fix and retest findings, documentation, CI workflow, final gate, clean shutdown and evidence.

**LAB-100:** Complete implementation only when all mandatory requirements and applicable local acceptance rows are satisfied on the final commit, the full local release-check succeeds, no lab runtime remains active, disk accounting stays in budget, the Git worktree is clean, and the requirement-to-test matrix is complete. Actual CI status is a separate publication condition; report it precisely. No implicit promotion or release. A missing prerequisite or failed mandatory gate is BLOCKED, not a completed implementation. Keep failing evidence and describe exact remediation; stop safely rather than repeatedly retrying a resource-heavy action.

The final run record includes branch/commit, spec SHA-256, pinned emulator asset/version/checksum, dependencies, host and isolation mechanism, every acceptance row and command result, unit/integration counts, CI run reference/status or blocker, actual CPU/RAM/disk and wall-clock measurements, issue dispositions, outstanding findings and cleanup confirmation. State whether the local release gate and publication conditions are satisfied separately. No fabricated test evidence, security guarantees, performance claims or future remote readiness.

## 12. Definition of success

A developer on the qualified local host can install the lab, provision a disposable AWS API environment, create and independently observe a small connected topology, exercise controlled mutations, reset to an empty or reproducible fixture, and remove it safely, without real AWS credentials or any AWS request leaving the contained runtime. This is achieved without compiling Ono or the emulator, while respecting the existing development workload. A future VM deployment uses the same conceptual lifecycle and fixtures; remote access and actual Ono provider integration remain separate, explicit work.
