import copy
import json

import pytest

from ono_sendai_lab import ledger
from ono_sendai_lab.errors import LabError
from ono_sendai_lab.install import check_platform, load_lock, verify_install


def _write_lock(tmp_path, repo_root, mutate):
    lock = json.loads((repo_root / "locks" / "emulator.lock.json").read_text())
    mutated = copy.deepcopy(lock)
    mutate(mutated)
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(mutated))
    return path


def test_real_lock_is_valid(repo_root):
    lock = load_lock(repo_root / "locks" / "emulator.lock.json")
    assert lock["version"] == "1.0.598"
    assert "latest" not in lock["asset"]["url"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["asset"].update(url=d["asset"]["url"].replace("https://", "http://")),
        lambda d: d["asset"].update(url="https://downloads.aws.com/aws-emulator/packages/aws-emulator/LTS/latest/aws-emulator_amd64.deb"),
        lambda d: d["asset"].update(url="https://example.com/aws-emulator.deb"),
        lambda d: d["asset"].update(sha256="nothex"),
        lambda d: d["asset"].pop("sha256"),
        lambda d: d["signature"].update(publisher_key_fingerprint="abc"),
        lambda d: d.update(version="latest"),
        lambda d: d["executable"].update(version_output=[]),
    ],
)
def test_lock_validation_rejects(tmp_path, repo_root, mutate):
    with pytest.raises(LabError) as exc:
        load_lock(_write_lock(tmp_path, repo_root, mutate))
    assert exc.value.code == "install_invalid"


def test_platform_mismatch_rejected(tmp_path, repo_root):
    lock = load_lock(_write_lock(tmp_path, repo_root, lambda d: d["platform"].update(machine="aarch64")))
    with pytest.raises(LabError) as exc:
        check_platform(lock)
    assert exc.value.code == "preflight_failed"


def test_verify_install_without_receipt(settings):
    with pytest.raises(LabError) as exc:
        verify_install(settings)
    assert exc.value.code == "install_invalid"


def test_ledger_admission_refuses_over_budget(settings):
    from dataclasses import replace

    small = replace(settings, disk_budget_bytes=300_000_000)
    with pytest.raises(LabError) as exc:
        ledger.admit(small, 100_000_000, "test")
    assert exc.value.code == "disk_budget_exceeded" and exc.value.exit_code == 4
    snap = ledger.admit(settings, 1_000_000, "test")
    assert snap["hard_quota"] is False and snap["projected_bytes"] == 1_000_000


def test_ledger_counts_lab_locations(settings):
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "blob").write_bytes(b"x" * 100_000)
    snap = ledger.measure(settings)
    assert snap["locations"]["data"] >= 100_000


def test_uninstall_fails_closed_without_systemd(settings):
    """Review m8: an unreachable user manager is no proof that no unit runs."""
    from dataclasses import replace

    from ono_sendai_lab.bootstrap import uninstall

    blind = replace(settings, simulate_missing=frozenset({"systemd"}))
    with pytest.raises(LabError) as exc:
        uninstall(blind)
    assert exc.value.code == "resource_control_unavailable"


def test_evidence_must_name_unchanged_code(tmp_path, monkeypatch):
    """Regression: the code hash was taken after the run, so a change during
    acceptance would have been reported as tested."""
    import json

    from ono_sendai_lab import devtools

    monkeypatch.setattr(devtools, "code_hash", lambda: "b" * 64)
    doc = {
        "code_hash": "b" * 64,
        "code_hash_at_end": "c" * 64,
        "spec_sha256": devtools.SPEC_SHA256,
        "acceptance_rows": {a: {"status": "passed"} for a in devtools.ACCEPTANCE_IDS},
        "cleanup": {"verified": True},
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(doc))
    assert "the code changed while acceptance was running" in devtools.check_evidence(path)
    doc["code_hash_at_end"] = "b" * 64
    path.write_text(json.dumps(doc))
    assert devtools.check_evidence(path) == []
