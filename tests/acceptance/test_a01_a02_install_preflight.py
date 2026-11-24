"""A01 bootstrap/provenance and A02 unsafe preflight (LAB-010..013, LAB-023, LAB-030)."""

import json
import os
import subprocess

import pytest
from labharness import ROOT, RUN_DIR, active_lab_units, base_env

BOOT = ROOT / "scripts" / "bootstrap.sh"


def boot(*args, env=None, timeout=900):
    proc = subprocess.run([str(BOOT), "--json", *args], capture_output=True, text=True, env=env or base_env(), timeout=timeout)
    return proc.returncode, json.loads(proc.stdout)


def snapshot(path):
    out = {}
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            st = os.lstat(os.path.join(root, name))
            out[os.path.join(root, name)] = (st.st_size, st.st_mtime_ns)
    return out


def data_dir():
    return os.path.join(base_env()["XDG_DATA_HOME"], "ono-sendai-lab")


def lock():
    return json.loads((ROOT / "locks" / "emulator.lock.json").read_text())


@pytest.mark.row("A01")
def test_A01_plan_changes_nothing():
    before = snapshot(data_dir())
    rc, doc = boot("--plan")
    assert rc == 0 and doc["result"]["dry_run"] is True
    steps = [s["step"] for s in doc["result"]["plan"]]
    assert "verify-openpgp-signature" in steps and "verify-asset-sha256" in steps
    assert doc["result"]["preflight"]["platform"]["machine"] == "x86_64"
    assert snapshot(data_dir()) == before


@pytest.mark.row("A01")
def test_A01_repeat_install_is_idempotent_and_reverifies():
    rc, doc = boot()
    assert rc == 0, doc
    res = doc["result"]
    assert res["signature"]["verified"] is True
    assert res["signature"]["fingerprint"].replace(" ", "") == lock()["signature"]["publisher_key_fingerprint"]
    assert res["executable"] == {"reused": True, "identity_verified": True}
    assert res["runtime_venv"]["reused"] is True
    assert res["receipt"]["executable_sha256"] == lock()["executable"]["sha256"]


def _mutated_lock(tmp_name, mutate):
    data = lock()
    mutate(data)
    path = RUN_DIR / tmp_name
    path.write_text(json.dumps(data))
    return path


@pytest.mark.row("A01")
def test_A01_corrupt_digest_rejected_without_change():
    receipt = os.path.join(data_dir(), "install-receipt.json")
    before = os.stat(receipt).st_mtime_ns
    bad = _mutated_lock("bad-digest.lock.json", lambda d: d["asset"].update(sha256="0" * 64))
    rc, doc = boot("--lock", str(bad), "--offline")
    assert rc == 4 and doc["error"]["code"] == "install_invalid"
    assert doc["error"]["details"]["actual_sha256"] == lock()["asset"]["sha256"]
    assert os.stat(receipt).st_mtime_ns == before


@pytest.mark.row("A01")
def test_A01_wrong_architecture_rejected():
    bad = _mutated_lock("bad-arch.lock.json", lambda d: d["platform"].update(machine="aarch64"))
    rc, doc = boot("--lock", str(bad), "--offline", "--plan")
    assert rc == 4 and doc["error"]["code"] == "preflight_failed"


@pytest.mark.row("A01")
def test_A01_fresh_install_doctor_and_uninstall_cycle():
    """A complete install into empty locations, a safe doctor, then a clean uninstall."""
    fresh = RUN_DIR / "fresh"
    env = base_env()
    env.update(XDG_DATA_HOME=str(fresh / "data"), XDG_STATE_HOME=str(fresh / "state"), XDG_CACHE_HOME=str(fresh / "cache"))
    downloads = fresh / "cache" / "ono-sendai-lab" / "downloads"
    downloads.mkdir(parents=True, mode=0o700)
    os.chmod(fresh / "cache" / "ono-sendai-lab", 0o700)
    cached = os.path.join(base_env()["XDG_CACHE_HOME"], "ono-sendai-lab", "downloads")
    name = lock()["asset"]["filename"]
    for f in (name, name + ".sig"):
        os.link(os.path.join(cached, f), downloads / f)  # no second download, no extra bytes
    rc, doc = boot("--offline", env=env)
    assert rc == 0, doc
    assert doc["result"]["executable"] == {"reused": False}
    assert doc["result"]["runtime_venv"]["reused"] is False
    proc = subprocess.run([str(ROOT / "bin" / "aws-lab"), "--json", "doctor"], capture_output=True, text=True, env=env, timeout=300)
    assert proc.returncode == 0, proc.stdout
    assert json.loads(proc.stdout)["result"]["checks"]["install"]["full_digest_checked"] is True
    rc, doc = boot("--uninstall", env=env)
    assert rc == 0 and set(doc["result"]["removed_locations"]) >= {"data", "state", "cache"}
    for sub in ("data", "state", "cache"):
        assert not (fresh / sub / "ono-sendai-lab").exists()


@pytest.mark.row("A02")
@pytest.mark.parametrize(
    "missing,code",
    [(["bwrap"], "isolation_unavailable"), (["systemd"], "resource_control_unavailable"), (["cgroup"], "resource_control_unavailable")],
)
def test_A02_doctor_detects_missing_support(lab, missing, code):
    err = lab.fail(code, 4, "doctor", config={"simulate_missing": missing})
    assert err["details"]["safe"] is False and err["details"]["problems"]


@pytest.mark.row("A02")
@pytest.mark.parametrize("missing", [["bwrap"], ["systemd"], ["cgroup"]])
def test_A02_unsafe_preflight_refuses_create_without_side_effects(lab, missing):
    runtime_root = os.path.join(os.environ["XDG_RUNTIME_DIR"], "ono-sendai-lab")
    before_runtime = set(os.listdir(runtime_root)) if os.path.isdir(runtime_root) else set()
    res = lab.cli("create", "--name", "a02-unsafe", config={"simulate_missing": missing})
    assert res.rc == 4 and res.error["code"] in ("isolation_unavailable", "resource_control_unavailable", "preflight_failed")
    assert lab.ok("status")["sandboxes"] == []
    assert active_lab_units() == []
    after_runtime = set(os.listdir(runtime_root)) if os.path.isdir(runtime_root) else set()
    assert after_runtime == before_runtime


@pytest.mark.row("A02")
def test_A02_doctor_is_safe_on_the_qualified_host(lab):
    res = lab.ok("doctor")
    checks = res["checks"]
    assert checks["network_file_isolation"]["interfaces"] == ["lo"]
    assert checks["network_file_isolation"]["external_probe"]["result"] == "ENETUNREACH"
    assert checks["resource_controls"]["limits"]["memory.max"] == "1073741824"
    assert checks["install"]["full_digest_checked"] is True
    (RUN_DIR / "doctor.json").write_text(json.dumps(res, indent=2, sort_keys=True))
