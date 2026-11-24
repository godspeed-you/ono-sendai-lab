# Security model and threat model

The lab qualifies its own controls. It is no evidence of AWS security: the emulator never denies a request signed with its own key, does not evaluate bucket or KMS key policies and has no cross-account or cross-region model. A passing emulator test says nothing about IAM correctness in production.

## Assets

Emulator-generated keys and CA (per generation), the operator's real cloud credentials and files (must stay untouched), the host network, the operator's home and workspaces, the host's resources.

## Threats and controls

| Actor | Example | Prevented by | Status |
| --- | --- | --- | --- |
| **Accidental misuse** by an agent or operator | `--endpoint-url`, `--profile`, `file://` input, a typo in a sandbox name, a second concurrent start (also from another state directory), a forgotten sandbox | CLI allowlist and per-kind validation (`operations.py`), name pattern, per-user slot lock plus unit census, lease + idle + absolute lifetime enforced in the unit, JSON errors with remedies | tested (A10, A14, A13, A16; cross-state-directory race in unit tests) |
| **Compromised or misbehaving upstream process** (the emulator is untrusted software) | reaching the internet or the host LAN, reading `~/.aws` or SSH keys, using the Docker socket, hogging memory/CPU, persisting after a crash | kernel isolation: empty network namespace (`lo` only), tmpfs root with a read-only allowlist, no capabilities, `no_new_privs`, nested user namespaces disabled, cgroup caps, `RuntimeMaxSec`, OOM policy | tested (A11, A04, A12, A13) |
| **Same-UID host attacker** | another process of the operator's user reading `$XDG_STATE_HOME`, connecting to the worker socket, stopping units | owner-private files and sockets protect against other users only; a same-UID process has the same rights as the lab | **not prevented**, by design (LAB-022) |
| **Future multi-user remote attacker** | a second developer reaching another's sandbox on the shared VM | nothing yet: needs authenticated transport, per-user authorization, ownership and quotas (ADR-0005) | **not implemented**; remote access is disabled |
| **Real cloud authorization** | a request reaching AWS with real credentials | the managed client is built from emulator keys only, ignores every ambient variable and file, refuses any URL off the emulator endpoint, and the network namespace has no route; real AWS files are never read, mounted or inspected | tested with synthetic sentinels (A10, A11); no real AWS address is contacted |

## What the kernel enforces and what the CLI enforces

- **Kernel:** network reachability, filesystem visibility, capabilities, CPU/memory/swap/tasks caps. These hold even if the lab's own Python code in the sandbox were subverted.
- **systemd (outside the sandbox):** the absolute lifetime (`RuntimeMaxSec`) and the OOM stop policy; they hold even if the supervisor hangs.
- **CLI and worker:** which AWS operations and parameters are allowed, generation binding, fixture state, redaction. A process of the operator's own UID can bypass these by talking to the emulator through the worker socket or reading files on the host side; they protect against mistakes, not against that process.

## Secrets handling

Keys and CA exist only in the generation's private tmpfs `HOME` inside the namespace and in the supervisor's memory; they vanish when the unit stops. No secret appears in argv, records, logs, JSON output, Git, issues or evidence: outputs carry references (`managed:/home/lab/.aws/emulator/PORT/…`), the CA SHA-256 and a 16-hex fingerprint of the access key id. Redaction runs on every output path and on upstream log lines before they are written; the gate runs a secret scan over the repository and the acceptance evidence.

## Residual risks and unproven areas

- Kernel or bubblewrap vulnerabilities can break containment; the lab makes no claim of protection against kernel exploits.
- No seccomp filter is applied beyond bubblewrap's defaults.
- `/usr` is visible read-only inside the sandbox (system binaries and libraries, no secrets by design of the distribution).
- The emulator may log request details in future versions. The lab keeps only allowlisted fields of upstream JSON log lines; upstream stderr lines are plain text, so they are pattern-redacted and truncated, which is weaker than field allowlisting.
- Same-UID access, as above.

Any failed or untested control in this table is a release blocker, reported in docs/STATE.md.
