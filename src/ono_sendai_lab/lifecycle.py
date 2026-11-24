"""Lifecycle manager: sandboxes, generations, leases, fixtures (LAB-033/034, LAB-040..043).

Every mutating command runs under the global lab lock, reconciles all records
first (interrupted operations, dead or expired runtimes, previous boots,
orphan units) and only then acts. At most one generation is active across
all sandboxes.
"""

from __future__ import annotations

import os
import signal
import time
from contextlib import contextmanager
from pathlib import Path

from . import __version__, isolation, ledger, operations, scenarios
from .backend import GenerationSpec, get_backend
from .config import LOGS_DEFAULT_LINES, LOGS_MAX_LINES, REGION, STARTUP_TIMEOUT_DEFAULT, STARTUP_TIMEOUT_MAX
from .errors import LabError
from .install import load_lock
from .local_backend import foreign_units
from .redact import redact_text
from .registry import ACTIVE_STATES, HISTORY_LIMIT, Registry, new_id, validate_name

START_DISK_PROJECTION = 20_000_000  # logs + runtime files of one generation (bounded)


class Interrupted(Exception):
    pass


@contextmanager
def interruptible():
    """Turn SIGINT/SIGTERM into an exception so cleanup paths run."""

    def handler(signum, _frame):
        raise Interrupted(signal.Signals(signum).name)

    import threading

    if threading.current_thread() is not threading.main_thread():
        yield  # signal handlers can only be installed in the main thread (library use)
        return
    old = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    for sig in old:
        signal.signal(sig, handler)
    try:
        yield
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)


class Lab:
    def __init__(self, settings):
        self.settings = settings
        self.registry = Registry(settings)

    # ------------------------------------------------------------------ util
    def backend_for(self, record: dict):
        return get_backend(record.get("backend", "local"), self.settings)

    def _runtime(self, record: dict) -> dict | None:
        gen = record.get("generation") or {}
        return gen.get("runtime")

    def _try_stop(self, record: dict, runtime: dict | None) -> bool:
        """Stop the runtime; True only when the backend verified its death."""
        if not runtime or runtime.get("ended"):
            return True
        try:
            self.backend_for(record).stop(runtime)
        except LabError as exc:
            record["cleanup_error"] = exc.message
            return False
        record.pop("cleanup_error", None)
        return True

    def _end_generation(self, record: dict, terminal: str, reason: str, stopped: bool = True) -> None:
        gen = record.get("generation") or {}
        hist = record.setdefault("history", [])
        if gen.get("id"):
            hist.append(
                {
                    "generation_id": gen["id"],
                    "number": gen.get("number"),
                    "final_state": terminal,
                    "reason": reason,
                    "ended_at": time.time(),
                }
            )
            del hist[:-HISTORY_LIMIT]
        if stopped:
            self._mark_ended(record)

    # ------------------------------------------------------------ reconcile
    def reconcile(self, record: dict) -> dict:
        """Bring a record in line with reality. Caller holds the lock."""
        state = record.get("state")
        runtime = self._runtime(record)
        changed = False
        if record.get("operation") and record["operation"].get("kind") == "seed":
            # The lock is free, so the seeding CLI died. Resources may be
            # half-created: the fixture is DIRTY; the runtime itself is
            # re-checked below like any READY generation.
            record.pop("operation")
            if record.get("fixture", {}).get("state") == "SEEDING":
                self.registry.set_fixture(record, "DIRTY", failed_operation={"interrupted": "seed"})
            self.registry.save(record)
            return self.reconcile(record)
        if record.get("operation"):
            op = record.pop("operation")
            changed = True
            stopped = self._try_stop(record, runtime)
            if state in ("STARTING", "STOPPING", "READY"):
                self.registry.set_state(record, "FAILED", f"interrupted {op.get('kind')}")
                self._end_generation(record, "FAILED", f"interrupted {op.get('kind')}", stopped=stopped)
                if record.get("fixture", {}).get("state") not in (None, "UNKNOWN"):
                    self.registry.set_fixture(record, "UNKNOWN")
        elif state == "READY" and runtime and not runtime.get("ended"):
            obs = self.backend_for(record).observe(runtime)
            gen = record["generation"]
            if obs.alive and time.time() >= gen["lease_deadline"]:
                obs.alive, obs.terminal_state, obs.reason = False, "EXPIRED", "lease-expired"
            if not obs.alive:
                if obs.reason == "stopping":
                    # The unit was still deactivating: let it finish, then classify from its exit record.
                    stopped = self._try_stop(record, runtime)
                    obs = self.backend_for(record).observe(runtime)
                stopped = self._try_stop(record, runtime)
                terminal = obs.terminal_state or "FAILED"
                if terminal == "STOPPED":
                    terminal = "FAILED"
                self.registry.set_state(record, terminal, obs.reason)
                record["diagnostics"] = obs.details
                self._end_generation(record, terminal, obs.reason, stopped=stopped)
                self.registry.set_fixture(record, "UNKNOWN")
                changed = True
        elif state in ("FAILED", "EXPIRED", "STOPPED") and runtime and not runtime.get("ended"):
            # An earlier stop could not be verified: the slot stays occupied until it can.
            if self._try_stop(record, runtime):
                self._mark_ended(record)
            changed = True
        elif state in ("STARTING", "STOPPING"):
            # A record in a transitional state without an operation marker is
            # corrupt; treat like an interrupted operation.
            record["operation"] = {"kind": "unknown"}
            return self.reconcile(record)
        if changed:
            self.registry.save(record)
        return record

    @staticmethod
    def _mark_ended(record: dict) -> None:
        runtime = (record.get("generation") or {}).get("runtime")
        if runtime:
            keep = ("backend", "unit", "description", "boot_id", "invocation_id", "generation_id", "ctl_dir")
            record["generation"]["runtime"] = {k: runtime.get(k) for k in keep}
            record["generation"]["runtime"]["ended"] = True

    def reconcile_all(self) -> list[dict]:
        return [self.reconcile(r) for r in self.registry.all()]

    def foreign(self, records: list[dict]) -> list[str]:
        known = {((r.get("generation") or {}).get("runtime") or {}).get("unit") for r in records}
        return foreign_units(self.settings, {u for u in known if u})

    def _active(self, records: list[dict]) -> list[dict]:
        """Records that occupy the single slot: active states, and any record
        whose runtime has not been verified dead."""
        out = []
        for r in records:
            runtime = (r.get("generation") or {}).get("runtime")
            if r.get("state") in ACTIVE_STATES or (runtime and not runtime.get("ended")):
                out.append(r)
        return out

    # ------------------------------------------------------------- commands
    def doctor(self) -> dict:
        import platform
        import sys

        from .install import os_release, verify_install

        checks: dict = {}
        problems: list[str] = []
        checks["lab_version"] = __version__
        checks["platform"] = {"machine": platform.machine(), "os_release": os_release(), "python": sys.version.split()[0]}
        try:
            lock = load_lock(self.settings.lock_path)
            checks["dependency_lock"] = {"emulator_version": lock["version"], "asset_sha256": lock["asset"]["sha256"]}
        except LabError as exc:
            problems.append(exc.message)
            lock = None
        try:
            checks["install"] = verify_install(self.settings, full_digest=True)
        except LabError as exc:
            checks["install"] = {"ok": False, "problems": exc.details.get("problems", [exc.message])}
            problems.append(exc.message)
        if self.settings.runtime_dir is None:
            problems.append("XDG_RUNTIME_DIR is not set")
        ns = isolation.probe_namespace(self.settings)
        checks["network_file_isolation"] = {
            k: ns.get(k) for k in ("ok", "reason", "problems", "interfaces", "external_probe", "remedy") if k in ns
        }
        if not ns.get("ok"):
            problems.append("network/file isolation unavailable: " + str(ns.get("reason") or ns.get("problems")))
        rc = isolation.probe_resource_controls(self.settings)
        checks["resource_controls"] = rc
        if not rc.get("ok"):
            problems.append("resource controls unavailable: " + str(rc.get("reason") or rc.get("problems")))
        try:
            checks["disk"] = ledger.measure(self.settings)
        except OSError as exc:
            problems.append(f"disk measurement failed: {type(exc).__name__}")
        try:
            records = self.registry.all()
            checks["sandboxes"] = {"count": len(records), "active": len(self._active(records)), "foreign_units": self.foreign(records)}
        except LabError as exc:
            problems.append(exc.message)
        checks["test_mode"] = self.settings.test_mode
        return {"safe": not problems, "problems": problems, "checks": checks, "backend": "local"}

    def scenarios(self) -> dict:
        lock = load_lock(self.settings.lock_path)
        compat = _load_compat(self.settings)
        out = []
        for manifest in scenarios.list_manifests(self.settings.scenarios_dir):
            ops = manifest["requires"]["operations"]
            status = {op: compat.get(op, "unqualified") for op in ops}
            out.append(
                {
                    "id": manifest["id"],
                    "version": manifest["version"],
                    "description": manifest["description"],
                    "required_operations": ops,
                    "operation_qualification": status,
                    "qualified_on": {"emulator": lock["version"], "region": REGION},
                    "resources": [r["logical_id"] for r in manifest["resources"]],
                }
            )
        unsupported = ["aws-degraded", "aws-enterprise", "aws-scale"]
        return {"scenarios": out, "future_proposals_not_supported": unsupported}

    def _new_generation_spec(self, record: dict, startup_timeout: int) -> GenerationSpec:
        now = time.time()
        number = (record.get("generation") or {}).get("number", 0) + 1
        return GenerationSpec(
            sandbox_id=record["sandbox_id"],
            generation_id=new_id(),
            number=number,
            region=REGION,
            started_at=now,
            lease_deadline=now + self.settings.lease_seconds,
            max_deadline=now + self.settings.max_lifetime_seconds,
            idle_seconds=self.settings.idle_seconds,
            startup_timeout=startup_timeout,
            test_hooks=self.settings.test_mode,
        )

    def _start_generation(self, record: dict, startup_timeout: int) -> None:
        """STARTING -> READY with EMPTY fixture, or FAILED with cleanup."""
        backend = self.backend_for(record)
        spec = self._new_generation_spec(record, startup_timeout)
        record["generation"] = {
            "id": spec.generation_id,
            "number": spec.number,
            "started_at": spec.started_at,
            "lease_deadline": spec.lease_deadline,
            "max_deadline": spec.max_deadline,
            "region": spec.region,
            "startup_timeout": startup_timeout,
            "runtime": None,
        }
        record.pop("baseline", None)
        self.registry.set_state(record, "STARTING", "starting generation")
        self.registry.set_fixture(record, "UNKNOWN", scenario=None, mapping={}, failed_operation=None, verification=None)
        record["operation"] = {"kind": "start", "pid": os.getpid(), "started_at": time.time()}
        self.registry.save(record)
        record["generation"]["runtime"] = backend.prepare(spec)
        self.registry.save(record)
        try:
            runtime = backend.start(spec, record["generation"]["runtime"], self.registry.logs_dir(record))
            record["generation"]["runtime"] = runtime
            self.registry.save(record)
            baseline = backend.call(runtime, "baseline", {}, timeout=60)
            record["baseline"] = baseline
            self.registry.set_fixture(record, "EMPTY", scenario=None)
            self.registry.set_state(record, "READY", "verified health, identity, isolation and limits")
            record.pop("operation", None)
            self.registry.save(record)
        except (LabError, Interrupted, Exception) as exc:
            stopped = self._try_stop(record, record["generation"].get("runtime"))
            reason = (
                exc.code if isinstance(exc, LabError) else f"interrupted ({exc})" if isinstance(exc, Interrupted) else type(exc).__name__
            )
            self.registry.set_state(record, "FAILED", reason)
            record["diagnostics"] = exc.details if isinstance(exc, LabError) else {}
            self._end_generation(record, "FAILED", reason, stopped=stopped)
            record.pop("operation", None)
            self.registry.save(record)
            if isinstance(exc, LabError):
                raise
            if isinstance(exc, Interrupted):
                raise LabError("lifecycle_failure", f"create/reset interrupted by {exc}; runtime stopped and recorded FAILED") from None
            raise LabError("internal_error", f"unexpected failure during start: {type(exc).__name__}") from exc

    def _stop_generation(self, record: dict, reason: str) -> None:
        runtime = self._runtime(record)
        state = record.get("state")
        if state in ("READY", "STARTING"):
            self.registry.set_state(record, "STOPPING", reason)
            record["operation"] = {"kind": "stop", "pid": os.getpid(), "started_at": time.time()}
            self.registry.save(record)
        if runtime and not runtime.get("ended"):
            self.backend_for(record).stop(runtime)
        if record.get("state") == "STOPPING":
            self.registry.set_state(record, "STOPPED", reason)
            self._end_generation(record, "STOPPED", reason)
        elif runtime and not runtime.get("ended"):
            self._end_generation(record, record.get("state"), reason)
        record.pop("operation", None)
        self.registry.save(record)

    def _admit_start(self, records: list[dict], startup_timeout: int, exclude_id: str | None = None) -> None:
        if not 1 <= startup_timeout <= STARTUP_TIMEOUT_MAX:
            raise LabError("invalid_argument", f"--startup-timeout must be 1..{STARTUP_TIMEOUT_MAX} seconds")
        active = [r for r in self._active(records) if r["sandbox_id"] != exclude_id]
        if active:
            raise LabError(
                "slot_busy",
                "another sandbox generation is active; v0.1.0 allows exactly one",
                {"active": [r["name"] for r in active], "remedy": "aws-lab destroy NAME or wait"},
            )
        foreign = self.foreign(records)
        if foreign:
            raise LabError(
                "slot_busy",
                "a lab generation not owned by this state directory is active",
                {"units": foreign, "remedy": "destroy it with the aws-lab state that created it, or `systemctl --user stop UNIT`"},
            )
        ledger.admit(self.settings, START_DISK_PROJECTION, "sandbox start")

    def create(self, name: str, startup_timeout: int = STARTUP_TIMEOUT_DEFAULT, backend: str = "local") -> dict:
        validate_name(name)
        backend_impl = get_backend(backend, self.settings)
        with self.registry.locked(), self.registry.slot_locked(), interruptible():
            records = self.reconcile_all()
            if any(r.get("name") == name for r in records):
                raise LabError(
                    "name_conflict", f"a sandbox named {name!r} already exists", {"remedy": "aws-lab reset NAME or aws-lab destroy NAME"}
                )
            self._admit_start(records, startup_timeout)
            preflight = backend_impl.preflight()
            record = {
                "schema": 1,
                "sandbox_id": new_id(),
                "name": name,
                "backend": backend,
                "created_at": time.time(),
                "test_mode": self.settings.test_mode,
                "state": None,
                "fixture": {},
                "history": [],
            }
            self._start_generation(record, startup_timeout)
            result = self.describe(record)
            result["preflight"] = {"containment": preflight["containment"], "resource_controls": preflight["resource_controls"]["limits"]}
            return result

    def status(self, name: str | None = None) -> dict:
        with self.registry.locked():
            records = self.reconcile_all()
        if name is not None:
            validate_name(name)
            match = [r for r in records if r.get("name") == name]
            if not match:
                raise LabError("not_found", f"no sandbox named {name!r}")
            out = self.describe(match[0])
            runtime = self._runtime(match[0])
            if match[0].get("state") == "READY" and runtime:
                try:
                    out["resources"] = self.backend_for(match[0]).inspect(runtime)
                    out["resources"]["processes"] = self.backend_for(match[0]).call(runtime, "metrics", {}, timeout=10)
                except LabError as exc:
                    out["diagnostics"] = {"inspect_error": exc.code}
            return out
        return {"sandboxes": [self.describe(r, brief=True) for r in records], "active_generations": len(self._active(records))}

    def describe(self, record: dict, brief: bool = False) -> dict:
        gen = record.get("generation") or {}
        runtime = gen.get("runtime") or {}
        fixture = record.get("fixture") or {}
        out = {
            "name": record["name"],
            "sandbox_id": record["sandbox_id"],
            "backend": record.get("backend"),
            "state": record.get("state"),
            "state_reason": record.get("state_reason"),
            "fixture": {
                "state": fixture.get("state"),
                "scenario": fixture.get("scenario"),
                "mapping": fixture.get("mapping") or {},
                "failed_operation": fixture.get("failed_operation"),
            },
            "generation": {
                "id": gen.get("id"),
                "number": gen.get("number"),
                "started_at": _iso(gen.get("started_at")),
                "lease_deadline": _iso(gen.get("lease_deadline")),
                "max_deadline": _iso(gen.get("max_deadline")),
                "region": gen.get("region"),
            },
            "created_at": _iso(record.get("created_at")),
            "test_mode": bool(record.get("test_mode")),
        }
        if not brief:
            out["diagnostics"] = record.get("diagnostics") or {}
            if record.get("cleanup_error"):
                out["diagnostics"]["cleanup_error"] = record["cleanup_error"]
            out["limits"] = runtime.get("limits")
            out["baseline"] = record.get("baseline")
            out["fixture"]["verification"] = fixture.get("verification")
            out["history"] = record.get("history", [])
        return out

    def _ready_record(self, name: str, generation: str | None) -> dict:
        records = self.reconcile_all()
        match = [r for r in records if r.get("name") == name]
        if not match:
            raise LabError("not_found", f"no sandbox named {name!r}")
        record = match[0]
        gen_id = (record.get("generation") or {}).get("id")
        if generation is not None and generation != gen_id:
            raise LabError("stale_generation", "the referenced generation is no longer current", {"current_generation": gen_id})
        self._refuse_unless_ready(record)
        return record

    def credentials(self, name: str, generation: str | None = None) -> dict:
        validate_name(name)
        with self.registry.locked():
            record = self._ready_record(name, generation)
        gen = record["generation"]
        runtime = gen["runtime"]
        startup = runtime.get("startup", {})
        return {
            "descriptor_version": 1,
            "sandbox": record["name"],
            "generation": gen["id"],
            "backend": record["backend"],
            "region": gen["region"],
            "endpoint": self.backend_for(record).endpoint_context(runtime),
            "authentication": {"mode": "sigv4", "account": startup.get("identity", {}).get("account")},
            "tls": {"ca_ref": startup.get("ca_ref"), "ca_sha256": startup.get("ca_sha256")},
            "credentials_ref": startup.get("credentials_ref"),
            "config_ref": startup.get("config_ref"),
            "access_key_fingerprint": startup.get("access_key_fingerprint"),
            "valid_until": _iso(gen["lease_deadline"]),
            "consumption": (
                "References are paths inside the managed context; secrets never leave it. "
                "Downstream tools run through `aws-lab aws` today; a future guarded execution path "
                "is described in docs/adr/0005-backend-boundary-and-future-vm.md."
            ),
        }

    def extend(self, name: str, minutes: int, generation: str | None = None) -> dict:
        validate_name(name)
        if not isinstance(minutes, int) or minutes < 1:
            raise LabError("invalid_argument", "--minutes must be a positive whole number")
        with self.registry.locked():
            record = self._ready_record(name, generation)
            gen = record["generation"]
            if time.time() >= gen["lease_deadline"]:
                raise LabError(
                    "lease_expired", "the lease has already ended; it cannot be revived", {"lease_deadline": _iso(gen["lease_deadline"])}
                )
            new_deadline = gen["lease_deadline"] + minutes * self.settings.minute_seconds
            if new_deadline > gen["max_deadline"]:
                raise LabError(
                    "lease_cap_exceeded",
                    "extension would pass the absolute lifetime of the generation",
                    {"max_deadline": _iso(gen["max_deadline"]), "lease_deadline": _iso(gen["lease_deadline"])},
                )
            self.backend_for(record).call(gen["runtime"], "set-lease", {"lease_deadline": new_deadline}, timeout=15)
            gen["lease_deadline"] = new_deadline
            self.registry.save(record)
            return {"name": name, "generation": gen["id"], "lease_deadline": _iso(new_deadline), "max_deadline": _iso(gen["max_deadline"])}

    def seed(self, name: str, scenario_id: str, generation: str | None = None) -> dict:
        validate_name(name)
        manifest = scenarios.load_manifest(self.settings.scenarios_dir, scenario_id)
        with self.registry.locked(), interruptible():
            record = self._ready_record(name, generation)
            fixture = record.get("fixture") or {}
            if fixture.get("state") != "EMPTY":
                raise LabError(
                    "fixture_state_conflict",
                    f"seed requires an EMPTY fixture; current state is {fixture.get('state')}",
                    {"remedy": f"aws-lab reset {name} --scenario {scenario_id}"},
                )
            ledger.admit(self.settings, 1_000_000, "seed")
            return self._seed(record, manifest)

    def _seed(self, record: dict, manifest: dict) -> dict:
        backend = self.backend_for(record)
        gen = record["generation"]
        runtime = gen["runtime"]
        labels = {"generation": gen["id"]}
        self.registry.set_fixture(record, "SEEDING", scenario=manifest["id"])
        record["operation"] = {"kind": "seed", "pid": os.getpid(), "started_at": time.time()}
        self.registry.save(record)
        try:
            outcome = backend.call(runtime, "seed", {"manifest": manifest, "labels": labels}, timeout=120)
            if not outcome.get("ok"):
                failed = outcome.get("failed", {})
                self.registry.set_fixture(
                    record,
                    "DIRTY",
                    mapping=outcome.get("mapping", {}),
                    failed_operation=failed,
                    verification=None,
                )
                record.pop("operation", None)
                self.registry.save(record)
                raise LabError(
                    "seed_failed",
                    f"seed of {manifest['id']} failed at {failed.get('logical_id')}; fixture is DIRTY, reset required",
                    {
                        "failed": failed,
                        "partial_mapping": outcome.get("mapping", {}),
                        "remedy": f"aws-lab reset {record['name']} --scenario {manifest['id']}",
                    },
                )
            check = backend.call(
                runtime,
                "verify",
                {"manifest": manifest, "labels": labels, "mapping": outcome["mapping"], "baseline": record.get("baseline", {})},
                timeout=120,
            )
        except Interrupted as exc:
            self.registry.set_fixture(record, "DIRTY", failed_operation={"interrupted": str(exc)})
            record.pop("operation", None)
            self.registry.save(record)
            raise LabError("lifecycle_failure", "seed interrupted; fixture is DIRTY") from None
        except LabError as exc:
            if record["fixture"].get("state") == "SEEDING":
                self.registry.set_fixture(record, "DIRTY", failed_operation={"error": exc.code})
                record.pop("operation", None)
                self.registry.save(record)
            raise
        passed = bool(check.get("passed"))
        verification = {
            "passed": passed,
            "assertions": len(check.get("assertions", [])),
            "failed_assertions": [a for a in check.get("assertions", []) if not a.get("passed")],
            "verified_at": _iso(time.time()),
        }
        self.registry.set_fixture(record, "VERIFIED" if passed else "DIRTY", mapping=outcome["mapping"], verification=verification)
        record.pop("operation", None)
        self.registry.save(record)
        if not passed:
            raise LabError(
                "verification_failed",
                f"{manifest['id']} verification failed; fixture is DIRTY",
                {"failed_assertions": verification["failed_assertions"]},
            )
        return {
            "name": record["name"],
            "generation": gen["id"],
            "scenario": manifest["id"],
            "fixture_state": "VERIFIED",
            "mapping": outcome["mapping"],
            "verification": {"passed": True, "assertions": verification["assertions"], "observed": check.get("observed")},
            "steps": outcome.get("steps", []),
        }

    def reset(self, name: str, scenario_id: str | None = None, startup_timeout: int = STARTUP_TIMEOUT_DEFAULT) -> dict:
        validate_name(name)
        manifest = scenarios.load_manifest(self.settings.scenarios_dir, scenario_id) if scenario_id else None
        with self.registry.locked(), self.registry.slot_locked(), interruptible():
            records = self.reconcile_all()
            match = [r for r in records if r.get("name") == name]
            if not match:
                raise LabError("not_found", f"no sandbox named {name!r}")
            record = match[0]
            old_generation = (record.get("generation") or {}).get("id")
            # Everything that can refuse runs before the old generation is touched.
            self._admit_start(records, startup_timeout, exclude_id=record["sandbox_id"])
            self.backend_for(record).preflight()
            self._stop_generation(record, "reset")
            self._start_generation(record, startup_timeout)
            result = {"previous_generation": old_generation, **self.describe(record)}
            if manifest is not None:
                result["seed"] = self._seed(record, manifest)
                result["fixture"] = self.describe(record)["fixture"]
            return result

    def destroy(self, name: str) -> dict:
        validate_name(name)
        with self.registry.locked(), interruptible():
            records = self.reconcile_all()
            match = [r for r in records if r.get("name") == name]
            if not match:
                return {"name": name, "destroyed": False, "absent": True}
            record = match[0]
            self._stop_generation(record, "destroy")
            self.registry.delete(record)
            return {"name": name, "destroyed": True, "absent": False, "sandbox_id": record["sandbox_id"]}

    def logs(self, name: str, tail: int = LOGS_DEFAULT_LINES) -> dict:
        validate_name(name)
        if not isinstance(tail, int) or not 1 <= tail <= LOGS_MAX_LINES:
            raise LabError("invalid_argument", f"--tail must be 1..{LOGS_MAX_LINES}")
        with self.registry.locked(shared=True):
            record = self.registry.get(name)
            directory = self.registry.logs_dir(record)
        lines: list[str] = []
        for fname in ("lab.log.1", "lab.log", "supervisor.stderr"):
            path = directory / fname
            if path.is_file() and not path.is_symlink():
                with open(path, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    fh.seek(max(0, size - 256 * 1024))
                    chunk = fh.read().decode(errors="replace")
                prefix = "supervisor-stderr " if fname == "supervisor.stderr" else ""
                lines.extend(prefix + line for line in chunk.splitlines() if line.strip())
        total = len(lines)
        selected = [redact_text(line)[:1000] for line in lines[-tail:]]
        return {"name": name, "lines": selected, "returned": len(selected), "available": total, "truncated": total > len(selected)}

    def aws(self, name: str, tokens: list[str], generation: str | None = None) -> dict:
        validate_name(name)
        op, _params = operations.parse_cli(tokens)
        with self.registry.locked(shared=not op.mutating):
            record = self._ready_record(name, generation) if op.mutating else self._ready_record_readonly(name, generation)
            runtime = record["generation"]["runtime"]
            if op.mutating:
                fixture = record.get("fixture") or {}
                if fixture.get("state") in ("EMPTY", "VERIFIED"):
                    self.registry.set_fixture(
                        record, "DIRTY", failed_operation=None, dirty_reason=f"user mutation {op.service} {op.cli_name}"
                    )
                    self.registry.save(record)
            result = self.backend_for(record).call(runtime, "aws", {"tokens": tokens}, timeout=90)
        result["fixture_state"] = (record.get("fixture") or {}).get("state")
        result["generation"] = record["generation"]["id"]
        return result

    def _ready_record_readonly(self, name: str, generation: str | None) -> dict:
        """Read-only path: no reconciliation writes under a shared lock."""
        record = self.registry.get(name)
        gen_id = (record.get("generation") or {}).get("id")
        if generation is not None and generation != gen_id:
            raise LabError("stale_generation", "the referenced generation is no longer current", {"current_generation": gen_id})
        self._refuse_unless_ready(record)
        obs = self.backend_for(record).observe(record["generation"]["runtime"])
        if time.time() >= record["generation"]["lease_deadline"] or obs.terminal_state == "EXPIRED":
            raise LabError(
                "lease_expired", "the sandbox generation expired; reset or destroy it", {"reason": obs.reason or "lease-expired"}
            )
        if not obs.alive:
            raise LabError("not_ready", "sandbox generation is no longer alive; run `aws-lab status` to reconcile", {"reason": obs.reason})
        return record

    @staticmethod
    def _refuse_unless_ready(record: dict) -> None:
        if record.get("state") == "EXPIRED":
            raise LabError("lease_expired", "the sandbox generation expired; reset or destroy it", {"reason": record.get("state_reason")})
        if record.get("state") != "READY":
            raise LabError("not_ready", f"sandbox is {record.get('state')}, not READY", {"reason": record.get("state_reason")})


def _iso(ts) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _load_compat(settings) -> dict:
    import json

    path = Path(settings.repo_root) / "docs" / "contracts" / "operation-compatibility.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {f"{e['service']} {e['operation']}": e["qualification_status"] for e in doc.get("operations", [])}
