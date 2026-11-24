# ADR-0004: Managed client adapter and worker channel

Status: accepted · 2026-11-24 · Requirements: LAB-024, LAB-025, LAB-026, LAB-051, LAB-070

## Decision

- The supervisor process inside the namespace is also the managed worker. It reads the emulator-generated keys from the private tmpfs `HOME`, builds one botocore session with explicit keys, the run CA as `verify`, endpoint `https://127.0.0.1:<port>` and region `us-west-2`, and keeps the keys in memory only.
- `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` point at a nonexistent path, metadata discovery and host prefix injection are disabled, proxy variables are removed, `proxies={}`, path-style S3 addressing, SigV4 (`s3v4`).
- A `before-send` hook refuses any request whose URL leaves the emulator endpoint, so neither a redirect nor a default-endpoint fallback can send a request elsewhere; the empty network namespace is the independent second layer.
- Retries: reads up to 3 attempts (botocore standard mode), mutations exactly 1 (no blind retry of ambiguous mutations). Connect timeout 5 s, read timeout 20 s.
- Startup proof before READY: verified-TLS health route (`ok`), an unsigned STS request rejected (403), telemetry notice says disabled, signed `GetCallerIdentity` returns account `000000000000` and `arn:aws:iam::000000000000:user/emulator`, and `DescribeAvailabilityZones` reports only `us-west-2`.
- Region adaptation: outside us-east-1 `CreateBucket` needs `LocationConstraint`; the lab sets it explicitly from the session region (qualified on 1.0.598).

## Worker channel

A filesystem Unix stream socket in the owner-private runtime directory (`$XDG_RUNTIME_DIR/ono-sendai-lab/<sandbox>/<generation>/worker.sock`, 0600). Frames are length-prefixed JSON, requests ≤ 64 KiB, responses ≤ 1 MiB. The server checks `SO_PEERCRED` against its own uid and every request's generation id. Operations: `ping`, `activate`, `set-lease`, `describe`, `aws`, `baseline`, `seed`, `verify`, `metrics`, and `probe` (test-mode sandboxes only). `aws` re-parses the allowlisted CLI-like tokens inside the sandbox with the same grammar as the host (`operations.py`). There is no TCP manager, no generic dispatcher and no command execution.

## Alternatives considered

Pointing an SDK on the host at the emulator would need a host-reachable listener, which the containment requirement excludes. A separate worker process entering the namespace with `nsenter` would add a PID-based race; one supervisor per generation keeps ownership in the unit.
