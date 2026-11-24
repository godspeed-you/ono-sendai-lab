"""Local backend: bubblewrap namespaces inside a transient systemd user unit.

Ownership evidence is the unit name (derived from sandbox and generation id),
its systemd InvocationID and the boot id recorded at start. Stopping goes
through ``systemctl --user stop`` of that unit only after the InvocationID
matches; the lab never signals a PID.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import ipc, isolation
from .backend import Backend, GenerationSpec, RuntimeObservation
from .errors import LabError
from .fsutil import ensure_private_dir, read_json_nofollow, remove_tree
from .install import emulator_dir, load_lock, verify_install
from .registry import boot_id

EXPIRED_REASONS = {"lease-expired", "max-lifetime", "idle-timeout"}


class LocalBackend(Backend):
    name = "local"

    def __init__(self, settings):
        self.settings = settings

    # -- preflight -------------------------------------------------------
    def preflight(self) -> dict:
        report: dict = {}
        report["install"] = verify_install(self.settings)
        if self.settings.runtime_dir is None:
            raise LabError("preflight_failed", "XDG_RUNTIME_DIR is not set; a systemd user session is required")
        ns = isolation.probe_namespace(self.settings)
        if not ns.get("ok"):
            raise LabError("isolation_unavailable", "network/file containment cannot be established", {"probe": _short(ns)})
        report["containment"] = {"ok": True, "interfaces": ns["interfaces"], "external_probe": ns["external_probe"]}
        rc = isolation.probe_resource_controls(self.settings)
        if not rc.get("ok"):
            raise LabError("resource_control_unavailable", "cgroup resource limits cannot be enforced", {"probe": rc})
        report["resource_controls"] = rc
        return report

    # -- helpers ---------------------------------------------------------
    def _ctl_dir(self, sandbox_id: str, generation_id: str) -> Path:
        assert self.settings.runtime_dir is not None
        return self.settings.runtime_dir / sandbox_id / generation_id

    def _supervisor_command(self, spec: GenerationSpec) -> list[str]:
        lock = load_lock(self.settings.lock_path)
        code = "import sys; sys.path.insert(0, '/lab/src'); from ono_sendai_lab.supervisor import main; sys.exit(main())"
        cmd = [
            f"{isolation.SANDBOX_VENV}/bin/python",
            "-I",
            "-c",
            code,
            "--generation",
            spec.generation_id,
            "--region",
            spec.region,
            "--startup-timeout",
            str(spec.startup_timeout),
            "--activation-grace",
            str(self.settings.activation_grace_seconds),
            "--lease-deadline",
            f"{spec.lease_deadline:.3f}",
            "--max-deadline",
            f"{spec.max_deadline:.3f}",
            "--idle-seconds",
            str(spec.idle_seconds),
            "--log-ring-bytes",
            str(self.settings.log_ring_bytes),
            "--expect-account",
            lock["qualified_identity"]["account"],
            "--expect-arn",
            lock["qualified_identity"]["arn"],
        ]
        if spec.test_hooks:
            cmd.append("--test-hooks")
            if self.settings.occupy_port:
                cmd.append("--occupy-port")
            if self.settings.seed_fault:
                cmd += ["--seed-fault", self.settings.seed_fault]
        return cmd

    # -- start -----------------------------------------------------------
    def start(self, spec: GenerationSpec, logs_dir) -> dict:
        lock = load_lock(self.settings.lock_path)
        assert self.settings.runtime_dir is not None
        ensure_private_dir(self.settings.runtime_dir)
        ensure_private_dir(self.settings.runtime_dir / spec.sandbox_id)
        ctl = ensure_private_dir(self._ctl_dir(spec.sandbox_id, spec.generation_id))
        logs_dir = ensure_private_dir(Path(logs_dir))
        unit = isolation.unit_name(spec.sandbox_id, spec.generation_id)
        tools = isolation.tool_paths(self.settings)
        inner = isolation.bwrap_argv(
            tools["bwrap"],
            emulator_dir=emulator_dir(self.settings, lock),
            venv_dir=self.settings.runtime_venv,
            src_dir=self.settings.src_dir,
            ctl_dir=ctl,
            logs_dir=logs_dir,
            command=self._supervisor_command(spec),
        )
        runtime_max = max(1, int(spec.max_deadline - time.time()))
        argv = isolation.systemd_run_argv(
            self.settings,
            unit,
            f"{isolation.DESCRIPTION_MARK} {spec.sandbox_id} generation {spec.generation_id}",
            runtime_max_sec=runtime_max,
            stderr_path=logs_dir / "supervisor.stderr",
            command=inner,
        )
        runtime = {
            "backend": "local",
            "unit": unit,
            "ctl_dir": str(ctl),
            "boot_id": boot_id(),
            "invocation_id": None,
            "control_group": None,
            "generation_id": spec.generation_id,
        }
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise LabError(
                "resource_control_unavailable", "could not create the transient systemd unit", {"reason": type(exc).__name__}
            ) from exc
        if proc.returncode != 0:
            self._stop_if_present(unit, None)
            raise LabError("resource_control_unavailable", "systemd refused the transient unit", {"stderr": proc.stderr.strip()[-300:]})
        props = isolation.show_unit(self.settings, unit)
        runtime["invocation_id"] = props.get("InvocationID") or None
        runtime["control_group"] = props.get("ControlGroup") or None
        try:
            if not runtime["invocation_id"] or not runtime["control_group"]:
                raise LabError("lifecycle_failure", "transient unit has no invocation identity")
            limits = isolation.read_cgroup_limits(self.settings, runtime["control_group"])
            problems = isolation.verify_limits(limits)
            if problems:
                raise LabError(
                    "resource_control_unavailable", "effective cgroup limits differ from the required caps", {"problems": problems}
                )
            runtime["limits"] = limits
            status = self._wait_ready(runtime, spec.startup_timeout + 10)
            runtime["startup"] = status.get("startup", {})
            act = ipc.call(self._socket(runtime), spec.generation_id, "activate", {"lease_deadline": spec.lease_deadline}, timeout=15)
            if not act.get("activated"):
                raise LabError("lifecycle_failure", "generation activation was not acknowledged")
        except BaseException:
            self.stop(runtime)
            raise
        return runtime

    def _socket(self, runtime: dict) -> str:
        return str(Path(runtime["ctl_dir"]) / "worker.sock")

    def _wait_ready(self, runtime: dict, timeout: float) -> dict:
        ctl = Path(runtime["ctl_dir"])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = _read_json(ctl / "status.json")
            if status and status.get("generation") == runtime["generation_id"]:
                if status.get("phase") == "ready" and (ctl / "worker.sock").exists():
                    return status
                if status.get("phase") in ("failed", "exited"):
                    exit_doc = self._wait_exit(ctl)
                    raise LabError(
                        "startup_failed",
                        "sandbox runtime failed during startup",
                        {"reason": status.get("reason") or (exit_doc or {}).get("reason"), "detail": (exit_doc or {}).get("detail")},
                    )
            props = isolation.show_unit(self.settings, runtime["unit"])
            if props.get("ActiveState") in ("failed", "inactive") or props.get("LoadState") == "not-found":
                exit_doc = self._wait_exit(ctl)
                raise LabError(
                    "startup_failed",
                    "sandbox runtime exited during startup",
                    {
                        "unit_result": props.get("Result"),
                        "reason": (exit_doc or {}).get("reason"),
                        "detail": (exit_doc or {}).get("detail"),
                    },
                )
            time.sleep(0.2)
        raise LabError("timeout", "sandbox did not become ready within the startup timeout", {"timeout_seconds": timeout})

    @staticmethod
    def _wait_exit(ctl: Path, wait: float = 3.0) -> dict | None:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            doc = _read_json(ctl / "exit.json")
            if doc:
                return doc
            time.sleep(0.1)
        return None

    # -- observe / stop --------------------------------------------------
    def observe(self, runtime: dict) -> RuntimeObservation:
        if runtime.get("boot_id") != boot_id():
            return RuntimeObservation(False, "FAILED", "host-reboot", {"note": "runtime from a previous boot; nothing was signalled"})
        ctl = Path(runtime["ctl_dir"])
        props = isolation.show_unit(self.settings, runtime["unit"])
        same = props.get("InvocationID") == runtime.get("invocation_id")
        active = props.get("ActiveState") in ("active", "activating", "deactivating", "reloading")
        if same and active and props.get("ActiveState") == "active":
            return RuntimeObservation(True, details={"unit_state": props.get("SubState")})
        exit_doc = _read_json(ctl / "exit.json") or {}
        reason = exit_doc.get("reason")
        result = props.get("Result") if same else None
        if result == "timeout":
            reason = "max-lifetime"
        elif result == "oom-kill":
            reason = "oom-kill"
        if same and active:
            return RuntimeObservation(False, "FAILED", "stopping", {"unit_state": props.get("ActiveState")})
        details = {"unit_result": result, "exit_reason": exit_doc.get("reason"), "detail": exit_doc.get("detail")}
        if reason in EXPIRED_REASONS:
            return RuntimeObservation(False, "EXPIRED", reason, details)
        if reason == "stop-requested":
            return RuntimeObservation(False, "STOPPED", reason, details)
        return RuntimeObservation(False, "FAILED", reason or "runtime-lost", details)

    def _stop_if_present(self, unit: str, invocation_id: str | None) -> dict:
        props = isolation.show_unit(self.settings, unit)
        if props.get("LoadState") == "not-found":
            return {"stopped": False, "present": False}
        if invocation_id is not None and props.get("InvocationID") not in (invocation_id, ""):
            raise LabError("lifecycle_failure", "unit identity does not match the recorded generation; refusing to stop it")
        isolation.stop_unit(self.settings, unit)
        return {"stopped": True, "present": True}

    def stop(self, runtime: dict) -> dict:
        if runtime.get("boot_id") and runtime.get("boot_id") != boot_id():
            self.cleanup(runtime)
            return {"stopped": False, "reason": "previous boot; runtime already gone"}
        result = self._stop_if_present(runtime["unit"], runtime.get("invocation_id"))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            props = isolation.show_unit(self.settings, runtime["unit"])
            if props.get("LoadState") == "not-found" or props.get("ActiveState") in ("inactive", "failed"):
                break
            time.sleep(0.2)
        else:
            raise LabError("cleanup_failed", "sandbox unit is still active after stop", {"unit": runtime["unit"]})
        isolation.systemctl(self.settings, "reset-failed", runtime["unit"])
        cg = runtime.get("control_group")
        if cg:
            procs = Path(isolation.tool_paths(self.settings)["cgroup_root"]) / cg.lstrip("/") / "cgroup.procs"
            if procs.exists() and procs.read_text().strip():
                raise LabError("cleanup_failed", "processes remain in the sandbox cgroup after stop")
        exit_doc = _read_json(Path(runtime["ctl_dir"]) / "exit.json")
        self.cleanup(runtime)
        result["verified_gone"] = True
        result["exit_reason"] = (exit_doc or {}).get("reason")
        return result

    def cleanup(self, runtime: dict) -> None:
        ctl = Path(runtime.get("ctl_dir", ""))
        if self.settings.runtime_dir and ctl.is_absolute() and self.settings.runtime_dir in ctl.parents:
            remove_tree(ctl)
            try:
                ctl.parent.rmdir()
            except OSError:
                pass

    # -- calls / inspect -------------------------------------------------
    def call(self, runtime: dict, op: str, params: dict | None = None, timeout: float = 90.0) -> dict:
        return ipc.call(self._socket(runtime), runtime["generation_id"], op, params, timeout=timeout)

    def inspect(self, runtime: dict) -> dict:
        info: dict = {"limits": runtime.get("limits")}
        cg = runtime.get("control_group")
        if cg:
            info["cgroup_usage"] = isolation.read_cgroup_usage(self.settings, cg)
        return info

    def endpoint_context(self, runtime: dict) -> dict:
        startup = runtime.get("startup", {})
        return {
            "url": startup.get("endpoint"),
            "context": "local-network-namespace",
            "reachable_from_host": False,
            "access": "aws-lab aws NAME -- ... (managed worker inside the namespace)",
        }


def _read_json(path: Path):
    try:
        return read_json_nofollow(path)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return None


def _short(report: dict) -> dict:
    return {k: report.get(k) for k in ("ok", "reason", "problems", "remedy", "stderr") if k in report}


def orphan_units(settings, known_units: set[str]) -> list[str]:
    """Lab-pattern units with the lab description marker that no record owns."""
    orphans = []
    for unit in isolation.list_lab_units(settings):
        name = unit.get("unit", "")
        if name in known_units or unit.get("active") not in ("active", "activating", "deactivating"):
            continue
        if str(unit.get("description", "")).startswith(isolation.DESCRIPTION_MARK):
            orphans.append(name)
    return orphans
