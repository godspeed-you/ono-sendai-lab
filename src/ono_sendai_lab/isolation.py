"""OS-level containment and resource controls (LAB-023, LAB-027, LAB-030; ADR-0002).

Containment: bubblewrap with fresh user, network, PID, IPC, UTS, cgroup and
mount namespaces; loopback is the only interface; the root filesystem is an
empty tmpfs with an explicit read-only allowlist.

Resource control: a transient systemd *user* service per generation, i.e. a
delegated cgroup v2 node with CPUQuota/MemoryMax/MemorySwapMax/TasksMax and a
RuntimeMaxSec absolute lifetime.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .errors import LabError

SANDBOX_HOME = "/home/lab"
SANDBOX_EMULATOR = "/opt/aws-emulator"
SANDBOX_VENV = "/lab/venv"
SANDBOX_SRC = "/lab/src"
SANDBOX_CTL = "/run/lab/ctl"
SANDBOX_LOGS = "/run/lab/logs"
UNIT_PREFIX = "ono-sendai-lab-"
UNIT_RE = re.compile(r"^ono-sendai-lab-[0-9a-f]{12}-[0-9a-f]{12}\.service$")
DESCRIPTION_MARK = "ono-sendai-lab sandbox"
CGROUP_ROOT = Path("/sys/fs/cgroup")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/+-]+$")


def tool_paths(settings) -> dict[str, str]:
    missing = settings.simulate_missing
    return {
        "bwrap": "/nonexistent/bwrap" if "bwrap" in missing else (shutil.which("bwrap") or "/usr/bin/bwrap"),
        "systemd-run": "/nonexistent/systemd-run" if "systemd" in missing else (shutil.which("systemd-run") or "/usr/bin/systemd-run"),
        "systemctl": "/nonexistent/systemctl" if "systemd" in missing else (shutil.which("systemctl") or "/usr/bin/systemctl"),
        "cgroup_root": "/nonexistent/cgroup" if "cgroup" in missing else str(CGROUP_ROOT),
    }


def safe_host_path(path: Path) -> str:
    """Host paths passed to bwrap/systemd must be absolute and shell-inert."""
    text = str(path)
    if not _SAFE_PATH.match(text) or "/../" in text or text.endswith("/.."):
        raise LabError("unsafe_configuration", "lab path contains unsupported characters", {"path_kind": "lab path"})
    return text


def bwrap_argv(
    bwrap: str,
    *,
    emulator_dir: Path | None,
    venv_dir: Path | None,
    src_dir: Path,
    ctl_dir: Path | None,
    logs_dir: Path | None,
    command: list[str],
    extra_env: dict[str, str] | None = None,
) -> list[str]:
    argv = [
        bwrap,
        "--unshare-all",
        "--unshare-user",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--hostname",
        "ono-sendai-lab",
        "--clearenv",
        "--setenv",
        "HOME",
        SANDBOX_HOME,
        "--setenv",
        "PATH",
        "/usr/bin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "EMULATOR_TELEMETRY",
        "off",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--setenv",
        "AWS_EC2_METADATA_DISABLED",
        "true",
    ]
    for key, value in sorted((extra_env or {}).items()):
        argv += ["--setenv", key, value]
    argv += [
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--perms",
        "0700",
        "--tmpfs",
        "/tmp",
        "--perms",
        "0700",
        "--tmpfs",
        SANDBOX_HOME,
        "--dir",
        "/run/lab",
        "--ro-bind",
        safe_host_path(src_dir),
        SANDBOX_SRC,
    ]
    if emulator_dir is not None:
        argv += ["--ro-bind", safe_host_path(emulator_dir), SANDBOX_EMULATOR]
    if venv_dir is not None:
        argv += ["--ro-bind", safe_host_path(venv_dir), SANDBOX_VENV]
    if ctl_dir is not None:
        argv += ["--bind", safe_host_path(ctl_dir), SANDBOX_CTL]
    if logs_dir is not None:
        argv += ["--bind", safe_host_path(logs_dir), SANDBOX_LOGS]
    argv += ["--chdir", SANDBOX_HOME, "--"]
    return argv + command


def selfcheck_command(python: str = "/usr/bin/python3") -> list[str]:
    code = "import sys; sys.path.insert(0, '/lab/src'); from ono_sendai_lab.selfcheck import main; sys.exit(main())"
    return [python, "-I", "-c", code]


def probe_namespace(settings, timeout: float = 20.0) -> dict:
    """Run the containment self-check in a throwaway bwrap namespace."""
    tools = tool_paths(settings)
    if not os.access(tools["bwrap"], os.X_OK):
        return {
            "ok": False,
            "reason": "bubblewrap (bwrap) is not installed or not executable",
            "remedy": "install the bubblewrap package (administrator action)",
        }
    argv = bwrap_argv(
        tools["bwrap"],
        emulator_dir=None,
        venv_dir=None,
        src_dir=settings.src_dir,
        ctl_dir=None,
        logs_dir=None,
        command=selfcheck_command(),
    )
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "namespace probe timed out"}
    if proc.returncode != 0 and not proc.stdout.strip():
        return {
            "ok": False,
            "reason": "bubblewrap could not create the unprivileged namespaces",
            "stderr": proc.stderr.strip()[-400:],
            "remedy": (
                "see docs/operations/troubleshooting.md#user-namespaces "
                "(the Ubuntu AppArmor userns restriction needs the bwrap-userns-restrict profile)"
            ),
        }
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "namespace probe produced no report", "stderr": proc.stderr.strip()[-400:]}
    return report


# --- systemd / cgroup -----------------------------------------------------


def unit_name(sandbox_id: str, generation_id: str) -> str:
    name = f"{UNIT_PREFIX}{sandbox_id[:12]}-{generation_id[:12]}.service"
    if not UNIT_RE.match(name):
        raise LabError("internal_error", "invalid unit name derived")
    return name


def systemd_run_argv(
    settings,
    unit: str,
    description: str,
    *,
    runtime_max_sec: int,
    stderr_path: Path,
    command: list[str],
) -> list[str]:
    tools = tool_paths(settings)
    props = [
        f"CPUQuota={100}%",
        f"MemoryMax={1_073_741_824}",
        "MemorySwapMax=0",
        "MemoryZSwapMax=0",
        "TasksMax=512",
        f"RuntimeMaxSec={int(runtime_max_sec)}",
        "TimeoutStopSec=15",
        "KillMode=control-group",
        "SendSIGKILL=yes",
        "OOMPolicy=stop",
        "Nice=10",
        "IOSchedulingClass=best-effort",
        "IOSchedulingPriority=7",
        "UMask=0077",
        "StandardInput=null",
        "StandardOutput=null",
        f"StandardError=truncate:{safe_host_path(stderr_path)}",
        "NoNewPrivileges=yes",
    ]
    argv = [tools["systemd-run"], "--user", "--quiet", f"--unit={unit}", f"--description={description}"]
    for prop in props:
        argv += ["-p", prop]
    return argv + ["--"] + command


SHOW_PROPS = [
    "LoadState",
    "ActiveState",
    "SubState",
    "Result",
    "InvocationID",
    "ControlGroup",
    "MainPID",
    "ExecMainStatus",
    "ExecMainCode",
    "Description",
    "RuntimeMaxUSec",
]


def systemctl(settings, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    tools = tool_paths(settings)
    try:
        return subprocess.run(
            [tools["systemctl"], "--user", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise LabError(
            "resource_control_unavailable", "systemctl is unavailable", {"remedy": "a systemd user session on cgroup v2 is required"}
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LabError("timeout", "systemctl did not answer in time") from exc


def show_unit(settings, unit: str) -> dict[str, str]:
    proc = systemctl(settings, "show", unit, *[f"--property={p}" for p in SHOW_PROPS])
    if proc.returncode != 0:
        raise LabError("resource_control_unavailable", "cannot query the systemd user manager", {"stderr": proc.stderr.strip()[-300:]})
    props = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    return props


def list_lab_units(settings) -> list[dict[str, str]]:
    proc = systemctl(settings, "list-units", "--all", "--plain", "--no-legend", "--output=json", f"{UNIT_PREFIX}*")
    if proc.returncode != 0:
        raise LabError("resource_control_unavailable", "cannot list systemd user units", {"stderr": proc.stderr.strip()[-300:]})
    try:
        units = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        units = []
    return [u for u in units if UNIT_RE.match(u.get("unit", ""))]


def stop_unit(settings, unit: str, timeout: float = 30.0) -> None:
    systemctl(settings, "stop", unit, timeout=timeout)
    systemctl(settings, "reset-failed", unit, timeout=timeout)


def read_cgroup_limits(settings, control_group: str) -> dict:
    root = Path(tool_paths(settings)["cgroup_root"])
    if not control_group.startswith("/") or ".." in control_group:
        raise LabError("resource_control_unavailable", "unit has no valid control group")
    base = root / control_group.lstrip("/")

    def read(name: str) -> str | None:
        try:
            return (base / name).read_text().strip()
        except OSError:
            return None

    return {
        "cpu.max": read("cpu.max"),
        "memory.max": read("memory.max"),
        "memory.swap.max": read("memory.swap.max"),
        "memory.zswap.max": read("memory.zswap.max"),
        "pids.max": read("pids.max"),
    }


def read_cgroup_usage(settings, control_group: str) -> dict:
    root = Path(tool_paths(settings)["cgroup_root"])
    base = root / control_group.lstrip("/")
    usage: dict = {}
    for name in ("memory.current", "memory.peak", "memory.swap.current"):
        try:
            usage[name] = int((base / name).read_text().strip())
        except (OSError, ValueError):
            usage[name] = None
    try:
        for line in (base / "cpu.stat").read_text().splitlines():
            key, value = line.split()
            if key in ("usage_usec", "nr_throttled", "throttled_usec"):
                usage[f"cpu.{key}"] = int(value)
    except OSError:
        pass
    try:
        for line in (base / "memory.events").read_text().splitlines():
            key, value = line.split()
            if key in ("oom", "oom_kill", "max"):
                usage[f"memory.events.{key}"] = int(value)
    except OSError:
        pass
    return usage


EXPECTED_LIMITS = {
    "cpu.max": "100000 100000",
    "memory.max": str(1_073_741_824),
    "memory.swap.max": "0",
}


def verify_limits(limits: dict) -> list[str]:
    problems = []
    for key, expected in EXPECTED_LIMITS.items():
        if limits.get(key) != expected:
            problems.append(f"{key} is {limits.get(key)!r}, expected {expected!r}")
    if limits.get("memory.zswap.max") not in (None, "0"):
        problems.append(f"memory.zswap.max is {limits.get('memory.zswap.max')!r}, expected '0'")
    if limits.get("pids.max") in (None, "max"):
        problems.append("pids.max is unbounded")
    return problems


def probe_resource_controls(settings, timeout: float = 30.0) -> dict:
    """Start a throwaway transient unit with the production limits and read
    the effective kernel values back. Creates no AWS resources."""
    tools = tool_paths(settings)
    if not os.access(tools["systemd-run"], os.X_OK) or not os.access(tools["systemctl"], os.X_OK):
        return {"ok": False, "reason": "systemd-run/systemctl unavailable", "remedy": "a systemd user session on cgroup v2 is required"}
    unit = f"{UNIT_PREFIX}probe-{os.getpid()}.service"
    argv = [tools["systemd-run"], "--user", "--quiet", "--wait", "--collect", f"--unit={unit}"]
    for prop in (
        "CPUQuota=100%",
        "MemoryMax=1073741824",
        "MemorySwapMax=0",
        "MemoryZSwapMax=0",
        "TasksMax=512",
        "RuntimeMaxSec=30",
    ):
        argv += ["-p", prop]
    # The unit reads its own effective cgroup values while it is alive.
    code = (
        "import json,sys,pathlib\n"
        "cg=open('/proc/self/cgroup').read().strip().split('::',1)[1]\n"
        "b=pathlib.Path(sys.argv[1])/cg.lstrip('/')\n"
        "def r(n):\n"
        "  try: return (b/n).read_text().strip()\n"
        "  except OSError: return None\n"
        "print(json.dumps({'cgroup':cg,'limits':{n:r(n) for n in "
        "('cpu.max','memory.max','memory.swap.max','memory.zswap.max','pids.max')}}))\n"
    )
    argv += ["--pipe", "--", "/usr/bin/python3", "-I", "-c", code, tools["cgroup_root"]]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"transient unit probe failed: {type(exc).__name__}"}
    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": "systemd user manager refused the transient unit",
            "stderr": proc.stderr.strip()[-300:],
            "remedy": "ensure `systemctl --user` works (logind session or lingering) and cgroup v2 delegation",
        }
    try:
        report = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "reason": "transient unit probe produced no report"}
    limits = report.get("limits", {})
    problems = verify_limits(limits)
    return {"ok": not problems, "limits": limits, "problems": problems}
