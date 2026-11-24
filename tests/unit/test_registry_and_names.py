import os
import stat
import threading
import time

import pytest

from ono_sendai_lab.errors import LabError
from ono_sendai_lab.registry import (
    FIXTURE_TRANSITIONS,
    TRANSITIONS,
    Registry,
    check_fixture_transition,
    check_transition,
    new_id,
    validate_name,
)


@pytest.mark.parametrize("name", ["a", "lab-1", "A" * 48, "x-y-z", "9lives"])
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "A" * 49, "../x", "a/b", ".", "..", "a b", "a_b", "-lead", "ümlaut", "a\nb", "a;rm", "a$b"])
def test_invalid_names(name):
    with pytest.raises(LabError) as exc:
        validate_name(name)
    assert exc.value.code == "invalid_name" and exc.value.exit_code == 2


def test_process_transitions_follow_contract():
    check_transition(None, "STARTING")
    check_transition("STARTING", "READY")
    check_transition("READY", "STOPPING")
    check_transition("STOPPING", "STOPPED")
    check_transition("READY", "EXPIRED")
    for bad in [(None, "READY"), ("READY", "STARTING"), ("STOPPED", "READY"), ("EXPIRED", "READY"), ("FAILED", "READY")]:
        with pytest.raises(LabError):
            check_transition(*bad)


def test_no_state_reaches_ready_without_starting():
    sources = [s for s, targets in TRANSITIONS.items() if "READY" in targets]
    assert sources == ["STARTING"]


def test_fixture_transitions():
    check_fixture_transition("EMPTY", "SEEDING")
    check_fixture_transition("SEEDING", "VERIFIED")
    check_fixture_transition("SEEDING", "DIRTY")
    check_fixture_transition("VERIFIED", "DIRTY")
    with pytest.raises(LabError):
        check_fixture_transition("DIRTY", "VERIFIED")
    with pytest.raises(LabError):
        check_fixture_transition("DIRTY", "SEEDING")
    assert "VERIFIED" not in FIXTURE_TRANSITIONS["DIRTY"]


def test_records_are_private_and_atomic(settings):
    reg = Registry(settings)
    rec = {"sandbox_id": new_id(), "name": "n1", "state": None}
    reg.save(rec)
    path = settings.sandboxes_dir / rec["sandbox_id"] / "record.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(settings.state_dir).st_mode) == 0o700
    assert reg.get("n1")["sandbox_id"] == rec["sandbox_id"]
    assert not list(path.parent.glob(".tmp-*"))


def test_symlinked_record_is_not_followed(settings, tmp_path):
    reg = Registry(settings)
    rec = {"sandbox_id": new_id(), "name": "n2", "state": None}
    reg.save(rec)
    path = settings.sandboxes_dir / rec["sandbox_id"] / "record.json"
    target = tmp_path / "elsewhere.json"
    target.write_text('{"name": "n2", "sandbox_id": "evil"}')
    path.unlink()
    path.symlink_to(target)
    assert reg.find("n2") is None  # unreadable via O_NOFOLLOW -> skipped


def test_lock_is_exclusive_and_times_out(settings):
    reg = Registry(settings)
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with reg.locked():
            entered.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    entered.wait(5)
    start = time.monotonic()
    with pytest.raises(LabError) as exc:
        with reg.locked(timeout=0.5):
            pass
    assert exc.value.code == "lock_timeout" and exc.value.retryable
    assert time.monotonic() - start < 3
    release.set()
    t.join()
    with reg.locked(timeout=1):
        pass
