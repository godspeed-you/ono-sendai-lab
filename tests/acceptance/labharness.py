"""Real acceptance harness (LAB-091): real emulator, real namespaces, real cgroups.

Runs only under scripts/acceptance.sh, which provides a fresh lab-owned run
directory (AWS_LAB_ACCEPTANCE_DIR) inside the lab cache, so every byte is
counted by the disk ledger. Each test destroys what it created, also on
failure. Every --json document is validated against the CLI schemas and
kept (redacted) for the A16 contract summary.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
CLI = ROOT / "bin" / "aws-lab"
RUN_DIR = Path(os.environ.get("AWS_LAB_ACCEPTANCE_DIR", "/nonexistent"))
CONTRACTS = ROOT / "docs" / "contracts"

if not RUN_DIR.is_dir():
    pytest.exit("acceptance tests must run through scripts/acceptance.sh (AWS_LAB_ACCEPTANCE_DIR missing)", returncode=4)


def _validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    env_schema = json.loads((CONTRACTS / "cli-envelope.schema.json").read_text())
    res_schema = json.loads((CONTRACTS / "cli-results.schema.json").read_text())
    registry = Registry().with_resources(
        [(env_schema["$id"], Resource.from_contents(env_schema)), (res_schema["$id"], Resource.from_contents(res_schema))]
    )
    return Draft202012Validator(env_schema, registry=registry)


VALIDATOR = _validator()


def base_env() -> dict:
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": os.environ["HOME"],
        "XDG_RUNTIME_DIR": os.environ["XDG_RUNTIME_DIR"],
        "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")),
        "XDG_CACHE_HOME": os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")),
        "XDG_STATE_HOME": str(RUN_DIR / "state"),
    }
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    return env


def append_jsonl(name: str, obj: dict) -> None:
    from ono_sendai_lab.redact import redact_obj

    with open(RUN_DIR / name, "a") as fh:
        fh.write(json.dumps(redact_obj(obj), sort_keys=True) + "\n")


class Result:
    def __init__(self, rc: int, doc: dict | None, stdout: str, stderr: str):
        self.rc, self.doc, self.stdout, self.stderr = rc, doc, stdout, stderr

    @property
    def result(self) -> dict:
        assert self.doc and self.doc["ok"], self.doc
        return self.doc["result"]

    @property
    def error(self) -> dict:
        assert self.doc and not self.doc["ok"], self.doc
        return self.doc["error"]


def run_measured(argv: list[str], env: dict, timeout: float = 300) -> tuple[int, str, str, int]:
    """Run one CLI call and return (rc, stdout, stderr, peak RSS of that process in KiB)."""
    import tempfile

    with tempfile.TemporaryFile(dir=RUN_DIR) as out, tempfile.TemporaryFile(dir=RUN_DIR) as err:
        child = subprocess.Popen(argv, stdout=out, stderr=err, env=env)
        deadline = time.monotonic() + timeout
        while True:
            pid, status, usage = os.wait4(child.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() > deadline:
                child.kill()
                raise TimeoutError(argv)
            time.sleep(0.02)
        child.returncode = os.waitstatus_to_exitcode(status)
        out.seek(0)
        err.seek(0)
        return child.returncode, out.read().decode(), err.read().decode(), usage.ru_maxrss


class LabRunner:
    def __init__(self, test_name: str):
        self.test = test_name
        self.env = base_env()
        self.created: set[str] = set()

    def popen(self, *args: str, config: dict | None = None, env: dict | None = None):
        return subprocess.Popen(
            [str(CLI), "--json", *args], env=self._env(config, env), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

    def _env(self, config, env):
        e = dict(self.env)
        if config is not None:
            e["AWS_LAB_TEST_MODE"] = "1"
            e["AWS_LAB_TEST_CONFIG"] = json.dumps(config)
        if env:
            e.update(env)
        return e

    def cli(self, *args: str, config: dict | None = None, env: dict | None = None, timeout: float = 300, as_json: bool = True) -> Result:
        argv = [str(CLI)] + (["--json"] if as_json else []) + list(args)
        if args and args[0] == "create" and "--name" in args:
            self.created.add(args[args.index("--name") + 1])
        proc = subprocess.run(argv, capture_output=True, text=True, env=self._env(config, env), timeout=timeout)
        doc = None
        if as_json:
            assert proc.stdout.count("\n") == 1, f"expected exactly one JSON document, got {proc.stdout!r} / {proc.stderr!r}"
            doc = json.loads(proc.stdout)
            errors = sorted(VALIDATOR.iter_errors(doc), key=lambda e: list(e.path))
            assert not errors, f"schema violation for {args[:2]}: {[e.message for e in errors[:3]]}"
            if doc["ok"]:
                assert proc.returncode == 0
            else:
                assert proc.returncode == doc["error"]["exit_code"]
            append_jsonl("cli-docs.jsonl", {"test": self.test, "args": list(args[:2]), "rc": proc.returncode, "doc": doc})
        return Result(proc.returncode, doc, proc.stdout, proc.stderr)

    def ok(self, *args, **kw) -> dict:
        res = self.cli(*args, **kw)
        assert res.rc == 0, (args, res.doc, res.stderr)
        return res.result

    def fail(self, code: str, exit_code: int, *args, **kw) -> dict:
        res = self.cli(*args, **kw)
        assert res.rc == exit_code and res.error["code"] == code, (args, res.doc)
        return res.error

    # -- independent observation (not through the CLI) ---------------------
    def record(self, name: str) -> dict:
        for path in (RUN_DIR / "state" / "ono-sendai-lab" / "sandboxes").glob("*/record.json"):
            doc = json.loads(path.read_text())
            if doc.get("name") == name:
                return doc
        raise KeyError(name)

    def runtime(self, name: str) -> dict:
        return self.record(name)["generation"]["runtime"]

    def emulator_pid(self, name: str) -> int:
        cg = self.runtime(name)["control_group"]
        for pid in Path(f"/sys/fs/cgroup{cg}/cgroup.procs").read_text().split():
            try:
                if Path(f"/proc/{pid}/comm").read_text().strip() == "aws-emulator":
                    return int(pid)
            except OSError:
                continue
        raise AssertionError("emulator process not found in the unit cgroup")

    def observe_ops(self, ops: list[str], outcome: str, scenario: str | None = None) -> None:
        for op in ops:
            append_jsonl("operations.jsonl", {"test": self.test, "operation": op, "outcome": outcome, "scenario": scenario})

    def cleanup(self) -> None:
        for name in sorted(self.created):
            subprocess.run([str(CLI), "--json", "destroy", name], capture_output=True, env=self.env, timeout=120)


def lab_units() -> list[dict]:
    out = subprocess.run(
        ["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend", "--output=json", "ono-sendai-lab-*"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    return [u for u in json.loads(out or "[]") if not u["unit"].startswith("ono-sendai-lab-probe")]


def active_lab_units() -> list[str]:
    return [u["unit"] for u in lab_units() if u["active"] in ("active", "activating", "deactivating")]


def unit_props(unit: str) -> dict:
    out = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState,LoadState,Result,InvocationID"], capture_output=True, text=True
    ).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def wait_until(predicate, timeout: float, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


class Listener:
    """Harmless sentinel TCP listener that only counts connection attempts."""

    def __init__(self, host: str = "127.0.0.1"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
                self.accepted += 1
                conn.close()
            except OSError:
                continue

    def close(self):
        self._stop.set()
        self.thread.join(2)
        self.sock.close()


@pytest.fixture
def lab(request):
    runner = LabRunner(request.node.name)
    yield runner
    runner.cleanup()
    assert not [u for u in active_lab_units()], "a lab unit survived its test"


# --- acceptance row bookkeeping ----------------------------------------------

_ROWS: dict[str, dict] = {}


def pytest_configure(config):
    config.addinivalue_line("markers", "row(id): acceptance matrix row covered by this test")


def pytest_runtest_logreport(report):
    if report.when not in ("setup", "call", "teardown"):
        return
    rows = [m.args[0] for m in getattr(report, "_rows", [])]
    for row in rows:
        entry = _ROWS.setdefault(row, {"tests": {}, "status": "passed"})
        prev = entry["tests"].get(report.nodeid, "passed")
        outcome = report.outcome if report.outcome != "passed" else prev
        if report.when == "call" or report.outcome != "passed":
            entry["tests"][report.nodeid] = outcome
            entry.setdefault("durations", {})[report.nodeid] = round(entry.get("durations", {}).get(report.nodeid, 0) + report.duration, 2)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    report._rows = list(item.iter_markers("row"))


def pytest_sessionfinish(session, exitstatus):
    for entry in _ROWS.values():
        entry["status"] = "passed" if entry["tests"] and all(v == "passed" for v in entry["tests"].values()) else "failed"
    (RUN_DIR / "rows.json").write_text(json.dumps(_ROWS, indent=2, sort_keys=True))


def kill_quietly(proc):
    try:
        proc.send_signal(signal.SIGKILL)
    except ProcessLookupError:
        pass
