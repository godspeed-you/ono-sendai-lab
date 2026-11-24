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
