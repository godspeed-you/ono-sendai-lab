"""A13 leases/expiry, A14 concurrency and interruption, A15 destroy and cleanup."""

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest
from labharness import RUN_DIR, active_lab_units, kill_quietly, unit_props, wait_until

FAST = {"minute_seconds": 1}  # lease 30 s, absolute 120 s, idle 15 s; same enforcement path


def _gone(unit):
    return unit_props(unit).get("ActiveState") != "active"


@pytest.mark.row("A13")
def test_A13_lease_expiry_enforced_after_cli_exit(lab):
    started = time.monotonic()
    lab.ok("create", "--name", "a13l", config=FAST)
    unit = lab.runtime("a13l")["unit"]
    # Keep the emulator busy so the 15 s idle timeout cannot fire first.
    while time.monotonic() - started < 24:
        lab.ok("aws", "a13l", "--", "sts", "get-caller-identity", config=FAST)
        time.sleep(4)
    # No CLI runs from here on; only systemd is observed.
    assert wait_until(lambda: _gone(unit), 30, 0.5)
    elapsed = time.monotonic() - started
    assert 28 <= elapsed <= 45, elapsed
    status = lab.ok("status", "a13l", config=FAST)
    assert status["state"] == "EXPIRED" and status["state_reason"] == "lease-expired"
    lab.fail("lease_expired", 3, "extend", "a13l", "--minutes", "1", config=FAST)
    lab.fail("lease_expired", 3, "aws", "a13l", "--", "s3", "list-buckets", config=FAST)
    lab.fail("lease_expired", 3, "credentials", "a13l", config=FAST)
    lab.ok("destroy", "a13l")


@pytest.mark.row("A13")
def test_A13_idle_timeout_not_kept_alive_by_status_polling(lab):
    started = time.monotonic()
    lab.ok("create", "--name", "a13i", config=FAST)
    unit = lab.runtime("a13i")["unit"]
    state = None
    while time.monotonic() - started < 28:
        state = lab.ok("status", "a13i", config=FAST)["state"]
        if state != "READY":
            break
        time.sleep(2)
    elapsed = time.monotonic() - started
    assert state == "EXPIRED", state
    assert elapsed < 28, "idle timeout must fire before the 30 s lease"
    assert lab.ok("status", "a13i", config=FAST)["state_reason"] == "idle-timeout"
    assert _gone(unit)
    lab.ok("destroy", "a13i")


@pytest.mark.row("A13")
def test_A13_extension_cap_and_absolute_lifetime(lab):
    started = time.monotonic()
    lab.ok("create", "--name", "a13m", config=FAST)
    unit = lab.runtime("a13m")["unit"]
    lab.fail("lease_cap_exceeded", 4, "extend", "a13m", "--minutes", "100", config=FAST)
    lab.fail("invalid_argument", 2, "extend", "a13m", "--minutes", "0", config=FAST)
    lab.fail("usage_error", 2, "extend", "a13m", "--minutes", "1.5", config=FAST)
    ext = lab.ok("extend", "a13m", "--minutes", "89", config=FAST)
    assert ext["lease_deadline"] <= ext["max_deadline"]
    lab.fail("lease_cap_exceeded", 4, "extend", "a13m", "--minutes", "2", config=FAST)
    while not _gone(unit) and time.monotonic() - started < 135:
        lab.cli("aws", "a13m", "--", "sts", "get-caller-identity", config=FAST)
        time.sleep(5)
    elapsed = time.monotonic() - started
    assert _gone(unit) and 115 <= elapsed <= 135, elapsed
    status = lab.ok("status", "a13m", config=FAST)
    assert status["state"] == "EXPIRED" and status["state_reason"] in ("max-lifetime", "lease-expired")
    lab.fail("lease_expired", 3, "extend", "a13m", "--minutes", "1", config=FAST)
    lab.ok("destroy", "a13m")


@pytest.mark.row("A14")
def test_A14_concurrent_creates_yield_one_generation(lab):
    procs = [lab.popen("create", "--name", f"a14-{i}") for i in range(3)]
    lab.created.update(f"a14-{i}" for i in range(3))
    results = [(p.wait(300), p.stdout.read()) for p in procs]
    codes = sorted(rc for rc, _ in results)
    assert codes == [0, 4, 4], results
    assert len(active_lab_units()) == 1
    ready = [s for s in lab.ok("status")["sandboxes"] if s["state"] == "READY"]
    assert len(ready) == 1
    for i in range(3):
        lab.ok("destroy", f"a14-{i}")


@pytest.mark.row("A14")
def test_A14_same_name_race(lab):
    procs = [lab.popen("create", "--name", "a14-same") for _ in range(2)]
    lab.created.add("a14-same")
    codes = sorted(p.wait(300) for p in procs)
    assert codes == [0, 3]
    assert len(active_lab_units()) == 1
    lab.ok("destroy", "a14-same")


def _wait_for_state(lab, name, state, timeout=60):
    def check():
        try:
            return lab.record(name).get("state") == state
        except (KeyError, ValueError):
            return False

    return wait_until(check, timeout, 0.05)


@pytest.mark.row("A14")
@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGINT])
def test_A14_interrupted_create_leaves_no_stale_ready(lab, sig):
    name = f"a14-int-{sig.name.lower()}"
    lab.created.add(name)
    proc = lab.popen("create", "--name", name)
    try:
        assert _wait_for_state(lab, name, "STARTING")
        wait_until(lambda: bool(active_lab_units()), 20, 0.05)
        proc.send_signal(sig)
        proc.wait(60)
    finally:
        kill_quietly(proc)
    status = lab.ok("status", name)
    assert status["state"] == "FAILED" and "interrupted" in status["state_reason"]
    assert active_lab_units() == []
    lab.ok("reset", name)  # recovery is an explicit new generation
    assert lab.ok("status", name)["state"] == "READY"
    lab.ok("destroy", name)


@pytest.mark.row("A14")
def test_A14_interrupted_reset_leaves_one_or_zero_generations(lab):
    lab.ok("create", "--name", "a14-reset")
    first = lab.record("a14-reset")["generation"]["id"]
    proc = lab.popen("reset", "a14-reset")
    try:
        assert wait_until(lambda: lab.record("a14-reset")["generation"]["id"] != first, 60, 0.05)
        proc.send_signal(signal.SIGKILL)
        proc.wait(30)
    finally:
        kill_quietly(proc)
    assert len(active_lab_units()) <= 1
    status = lab.ok("status", "a14-reset")
    assert status["state"] in ("FAILED", "READY")
    assert status["state"] != "READY" or status["generation"]["id"] != first
    assert len(active_lab_units()) == (1 if status["state"] == "READY" else 0)
    lab.ok("destroy", "a14-reset")


@pytest.mark.row("A14")
def test_A14_port_collision_on_reset(lab):
    lab.ok("create", "--name", "a14-port")
    old = lab.runtime("a14-port")["unit"]
    lab.fail("startup_failed", 5, "reset", "a14-port", config={"occupy_port": True})
    assert _gone(old)
    status = lab.ok("status", "a14-port")
    assert status["state"] == "FAILED"
    assert active_lab_units() == []
    lab.ok("destroy", "a14-port")


@pytest.mark.row("A15")
def test_A15_destroy_is_idempotent_and_complete(lab):
    lab.ok("create", "--name", "a15")
    lab.ok("seed", "a15", "--scenario", "aws-minimal")
    rec = lab.record("a15")
    assert lab.ok("destroy", "a15") == {"name": "a15", "destroyed": True, "absent": False, "sandbox_id": rec["sandbox_id"]}
    assert lab.ok("destroy", "a15") == {"name": "a15", "destroyed": False, "absent": True}
    assert active_lab_units() == []
    runtime_root = Path(os.environ["XDG_RUNTIME_DIR"]) / "ono-sendai-lab"
    assert not (runtime_root / rec["sandbox_id"]).exists()
    assert not (RUN_DIR / "state" / "ono-sendai-lab" / "sandboxes" / rec["sandbox_id"]).exists()
    emulators = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "aws-emulator"], capture_output=True, text=True).stdout.strip()
    assert emulators == "", "an emulator process (and with it its generated keys) survived destroy"
