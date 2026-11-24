"""Lifecycle registry: atomic, owner-private records under one lock (LAB-040, LAB-041).

States are normative; transitions are checked against
docs/contracts/lifecycle-states.json (mirrored in TRANSITIONS).
"""

from __future__ import annotations

import fcntl
import os
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import LabError
from .fsutil import atomic_write_json, ensure_private_dir, read_json_nofollow, remove_tree

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,47}$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
PROCESS_STATES = ("STARTING", "READY", "STOPPING", "STOPPED", "FAILED", "EXPIRED")
FIXTURE_STATES = ("EMPTY", "SEEDING", "VERIFIED", "DIRTY", "UNKNOWN")
ACTIVE_STATES = ("STARTING", "READY", "STOPPING")
TRANSITIONS = {
    None: {"STARTING"},
    "STARTING": {"READY", "FAILED", "STOPPING"},
    "READY": {"STOPPING", "FAILED", "EXPIRED"},
    "STOPPING": {"STOPPED", "FAILED"},
    "STOPPED": {"STARTING"},
    "FAILED": {"STARTING", "STOPPING", "STOPPED"},
    "EXPIRED": {"STARTING", "STOPPING", "STOPPED"},
}
FIXTURE_TRANSITIONS = {
    None: {"UNKNOWN", "EMPTY"},
    "UNKNOWN": {"EMPTY", "UNKNOWN"},
    "EMPTY": {"SEEDING", "DIRTY", "UNKNOWN", "VERIFIED"},
    "SEEDING": {"VERIFIED", "DIRTY", "UNKNOWN"},
    "VERIFIED": {"DIRTY", "UNKNOWN", "VERIFIED"},
    "DIRTY": {"UNKNOWN", "DIRTY"},
}
HISTORY_LIMIT = 10


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise LabError(
            "invalid_name",
            "sandbox names are 1-48 ASCII letters, digits or hyphens and start with a letter or digit",
            {"pattern": NAME_RE.pattern},
        )
    return name


def new_id() -> str:
    return secrets.token_hex(16)


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown"


def check_transition(old: str | None, new: str) -> None:
    if new not in TRANSITIONS.get(old, set()):
        raise LabError("lifecycle_failure", f"illegal lifecycle transition {old} -> {new}")


def check_fixture_transition(old: str | None, new: str) -> None:
    if new not in FIXTURE_TRANSITIONS.get(old, set()):
        raise LabError("lifecycle_failure", f"illegal fixture transition {old} -> {new}")


class Registry:
    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def locked(self, shared: bool = False, timeout: float | None = None):
        from .config import LOCK_TIMEOUT_SECONDS

        ensure_private_dir(self.settings.state_dir)
        fd = os.open(self.settings.global_lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        deadline = time.monotonic() + (LOCK_TIMEOUT_SECONDS if timeout is None else timeout)
        try:
            while True:
                try:
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise LabError("lock_timeout", "another lifecycle operation holds the lab lock") from None
                    time.sleep(0.1)
            yield
        finally:
            os.close(fd)

    @contextmanager
    def slot_locked(self, timeout: float | None = None):
        """Host-wide (per user) lock around slot admission and generation start.

        It lives in the runtime directory shared by every state directory of
        this user, so two registries cannot both pass admission and start a
        generation at the same time. Lock order: registry lock, then slot lock.
        """
        from .config import LOCK_TIMEOUT_SECONDS

        base = self.settings.runtime_dir or self.settings.state_dir
        ensure_private_dir(base)
        fd = os.open(base / "slot.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        deadline = time.monotonic() + (LOCK_TIMEOUT_SECONDS if timeout is None else timeout)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise LabError("lock_timeout", "another lab generation is being started") from None
                    time.sleep(0.1)
            yield
        finally:
            os.close(fd)

    def _dir(self, sandbox_id: str) -> Path:
        if not ID_RE.match(sandbox_id):
            raise LabError("internal_error", "invalid sandbox id")
        return self.settings.sandboxes_dir / sandbox_id

    def all(self) -> list[dict]:
        base = self.settings.sandboxes_dir
        if not base.exists():
            return []
        records = []
        for entry in sorted(base.iterdir()):
            if ID_RE.match(entry.name) and (entry / "record.json").exists():
                try:
                    records.append(read_json_nofollow(entry / "record.json"))
                except (OSError, ValueError):
                    continue
        return records

    def find(self, name: str) -> dict | None:
        validate_name(name)
        for rec in self.all():
            if rec.get("name") == name:
                return rec
        return None

    def get(self, name: str) -> dict:
        rec = self.find(name)
        if rec is None:
            raise LabError("not_found", f"no sandbox named {name!r}")
        return rec

    def save(self, record: dict) -> None:
        ensure_private_dir(self.settings.state_dir)
        ensure_private_dir(self.settings.sandboxes_dir)
        directory = ensure_private_dir(self._dir(record["sandbox_id"]))
        ensure_private_dir(directory / "logs")
        record["updated_at"] = time.time()
        atomic_write_json(directory / "record.json", record)

    def logs_dir(self, record: dict) -> Path:
        return self._dir(record["sandbox_id"]) / "logs"

    def delete(self, record: dict) -> None:
        remove_tree(self._dir(record["sandbox_id"]))

    def set_state(self, record: dict, new: str, reason: str = "") -> None:
        check_transition(record.get("state"), new)
        record["state"] = new
        record["state_reason"] = reason
        record["state_changed_at"] = time.time()

    def set_fixture(self, record: dict, new: str, **fields) -> None:
        fixture = record.setdefault("fixture", {})
        check_fixture_transition(fixture.get("state"), new)
        fixture.update(fields)
        fixture["state"] = new
        fixture["changed_at"] = time.time()
