"""Disk resource ledger and admission control (LAB-031).

There is no filesystem quota on the reference host, so the lab enforces the
5 GB budget by conservative admission: before every significant allocation it
measures all lab-owned locations plus registered tracked paths (the checkout
and run scratch), adds the projected allocation and a fixed reserve, and
refuses if the sum would exceed the budget. This is measurement, not a hard
quota; ADR-0002 records the limitation.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from .config import DISK_RESERVE_BYTES, MIN_HOST_FREE_BYTES
from .errors import LabError
from .fsutil import atomic_write_json, ensure_private_dir, read_json_nofollow, tree_size


def load_ledger(settings) -> dict:
    try:
        return read_json_nofollow(settings.ledger_path)
    except FileNotFoundError:
        return {"schema": 1, "tracked_paths": [], "baseline": {}, "created": None}


def init_ledger(settings, extra_paths: list[str]) -> dict:
    ensure_private_dir(settings.state_dir)
    ledger = load_ledger(settings)
    tracked = set(ledger.get("tracked_paths", []))
    tracked.add(str(settings.repo_root))
    for path in extra_paths:
        if os.path.isabs(path):
            tracked.add(path)
    ledger["tracked_paths"] = sorted(tracked)
    if not ledger.get("created"):
        ledger["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ledger["baseline"] = {
            "note": "lab-owned locations did not exist before the first bootstrap; their full size is attributable",
        }
    atomic_write_json(settings.ledger_path, ledger)
    return ledger


def measure(settings) -> dict:
    ledger = load_ledger(settings)
    locations = {name: tree_size(path) for name, path in settings.owned_locations().items()}
    tracked = {}
    owned = [p.resolve() for p in settings.owned_locations().values()]
    for path in ledger.get("tracked_paths", []):
        resolved = Path(path).resolve()
        if any(resolved == o or o in resolved.parents for o in owned):
            continue
        tracked[path] = tree_size(resolved)
    total = sum(locations.values()) + sum(tracked.values())
    probe = next((p for p in (settings.data_dir, settings.data_dir.parent, Path.home()) if p.exists()), Path("/"))
    free = shutil.disk_usage(probe).free
    return {
        "budget_bytes": settings.disk_budget_bytes,
        "reserve_bytes": DISK_RESERVE_BYTES,
        "used_bytes": total,
        "headroom_bytes": settings.disk_budget_bytes - total - DISK_RESERVE_BYTES,
        "locations": {k: v for k, v in locations.items()},
        "tracked_bytes": sum(tracked.values()),
        "tracked_count": len(tracked),
        "host_free_bytes": free,
        "hard_quota": False,
    }


def admit(settings, projected_bytes: int, purpose: str) -> dict:
    snapshot = measure(settings)
    need = snapshot["used_bytes"] + projected_bytes + DISK_RESERVE_BYTES
    if need > settings.disk_budget_bytes:
        raise LabError(
            "disk_budget_exceeded",
            f"{purpose} refused: projected lab disk use exceeds the budget",
            {
                "budget_bytes": settings.disk_budget_bytes,
                "used_bytes": snapshot["used_bytes"],
                "projected_bytes": projected_bytes,
                "reserve_bytes": DISK_RESERVE_BYTES,
            },
        )
    if snapshot["host_free_bytes"] < MIN_HOST_FREE_BYTES + projected_bytes:
        raise LabError(
            "disk_budget_exceeded",
            f"{purpose} refused: host free space below the safety floor",
            {"host_free_bytes": snapshot["host_free_bytes"], "floor_bytes": MIN_HOST_FREE_BYTES},
        )
    snapshot["projected_bytes"] = projected_bytes
    return snapshot
