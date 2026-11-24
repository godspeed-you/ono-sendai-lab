# Independent review record (A18 documentation review, plus code review)

Reviewer: independent read-only review agent (fresh context, Opus model), briefed with the spec and the file list only; it ran no emulator, installed nothing and wrote nothing.
Date: 2026-11-24
Reviewed state: `implementation` at 27ed65d plus the uncommitted documentation that became 63d5843.
Recorded by: the implementing agent, who summarised the reviewer's report below and applied the dispositions. The verdicts are the reviewer's; the dispositions are the implementer's and were verified by the tests named.

## A18 documentation verdicts

| Item | Reviewer verdict | Inconsistencies found | Disposition |
| --- | --- | --- | --- |
| Fresh install | actionable, mostly consistent | `--plan` wrote the state directory; `--offline` still built venvs from PyPI; the cgroup-delegation check in the prerequisites table did not test delegation | `--plan` writes nothing and `--offline` refuses when a venv must be built (code + A01 fresh-install test); delegation check added |
| Usage | actionable; flags, codes, exit classes, tag names and filter grammar match the CLI | `--json --help` printed text; slot enforcement described without the cross-state-directory race | `--json --help` prints one document (unit test); slot lock documented (ADR-0003, resource accounting) |
| Uninstall | actionable, consistent | refusal depended on systemctl being reachable | uninstall fails closed when the user manager cannot be queried (unit test) |
| Host prerequisites | consistent across README, installation, ADR-0002, ADR-0006, ci-runner | 26.04 qualified vs 24.04 primary is stated openly | unchanged; gap stays reported in STATE.md |
| Security limitations | honest overall | stderr "allowlisting" overstated; 404 accepted as auth proof; log bound "per generation"; lifetime backstop listed under kernel | docs corrected; auth proof now requires HTTP 403; supervisor stderr goes into the bounded ring log |
| VM migration | actionable as a design; ADR-0005 covers all five LAB-083 boundaries | none | unchanged |

Further documentation fixes: CONTRIBUTING venv path, AGENTS `admit` reference and leftover-unit route, STATE.md commit and evidence links.

## Code review findings and dispositions

| ID | Severity | Finding | Disposition | Evidence |
| --- | --- | --- | --- | --- |
| M1 | major | a failed stop still freed the slot and let `destroy` report success | runtime is marked ended only after a verified stop; unverified runtimes occupy the slot; reconcile retries; `destroy` fails with `cleanup_failed` | `test_unverified_stop_keeps_the_slot_and_is_retried` |
| M2 | major | the single-slot lock covered only one state directory | per-user slot lock in `$XDG_RUNTIME_DIR/ono-sendai-lab/slot.lock` around admission and start | `test_slot_lock_serialises_registries_sharing_one_runtime_dir` (fails with the lock removed) |
| m1 | minor | `reset` stopped the old generation before validating | validation, admission and preflight first | `test_reset_refusals_leave_the_running_generation_alone` |
| m2 | minor | deactivating unit classified FAILED; 5 s READY grace after the lease | re-observe after the stop; no grace | A13 tests |
| m3 | minor | `extend` racing the watchdog could revive a lease | CLI and supervisor refuse after the deadline | `test_extend_after_deadline_cannot_revive` |
| m4 | minor | emulator stderr only regex-redacted; `supervisor.stderr` unbounded | documented as pattern redaction; supervisor stderr redirected into the ring log | docs; A04 log bound |
| m5 | minor | 404 accepted as proof of enforced authentication | only 403 accepted | A03 (`unsigned_request_status: 403`) |
| m6 | minor | `--json --help` not JSON; `--json` missing from help | fixed | `test_json_help_is_one_document` |
| m7 | minor | `--plan` side effects; `--offline` misleading | fixed | A01 fresh-install test |
| m8 | minor | unit census treated an unreachable systemd as "no units" | `active_lab_units` and uninstall fail closed | `test_uninstall_fails_closed_without_systemd` |
| m9 | minor | credential-file leftover searches could never fail | replaced by checks for remaining `aws-emulator` processes and generation directories (credentials live only inside them) | `scripts/acceptance.sh`, `scripts/release-check.sh`, A15 |
| m10 | minor | A18 lacked this record | this file | A18 |

Areas the reviewer checked and found sound: name and path validation; private atomic writes and `O_NOFOLLOW` reads; no host code signals a PID; stops only through the owned unit after InvocationID or description match; reboot reconciliation; bwrap arguments and the in-namespace self-check; cgroup limits read back and fail closed; managed client (explicit keys and CA, endpoint guard, no mutation retries); IPC peer-credential and generation binding with size bounds; allowlist re-parsed in the worker; fixture DIRTY semantics; error-code mapping; bootstrap digest, signature and redirect checks; test channel can only shrink limits.
