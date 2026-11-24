"""Locations, limits and the test-only configuration channel.

Production values implement LAB-030/031/034/043. The test channel
(``AWS_LAB_TEST_MODE=1`` plus ``AWS_LAB_TEST_CONFIG`` JSON) can only shorten
durations, lower budgets, remove capabilities or enable read-only probes; it
can never raise a limit or weaken isolation (ADR-0003).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .errors import LabError

APP = "ono-sendai-lab"
REPO_ROOT = Path(__file__).resolve().parents[2]

MEMORY_MAX_BYTES = 1_073_741_824
CPU_QUOTA_PERCENT = 100
TASKS_MAX = 512
DISK_BUDGET_BYTES = 5_000_000_000
DISK_RESERVE_BYTES = 250_000_000
MIN_HOST_FREE_BYTES = 2_000_000_000
LEASE_MINUTES = 30
MAX_LIFETIME_MINUTES = 120
IDLE_MINUTES = 15
STARTUP_TIMEOUT_DEFAULT = 30
STARTUP_TIMEOUT_MAX = 60
STOP_TIMEOUT_SECONDS = 15
LOCK_TIMEOUT_SECONDS = 120
LOG_RING_BYTES = 262_144
LOGS_DEFAULT_LINES = 50
LOGS_MAX_LINES = 200
REGION = "us-west-2"

_TEST_KEYS = {
    "minute_seconds",
    "disk_budget_bytes",
    "simulate_missing",
    "occupy_port",
    "seed_fault",
    "log_ring_bytes",
    "activation_grace_seconds",
}
_MISSING_CAPS = {"bwrap", "systemd", "cgroup"}
_SEED_FAULTS = {"subnet-outside-vpc"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    state_dir: Path
    cache_dir: Path
    runtime_dir: Path | None
    repo_root: Path = REPO_ROOT
    minute_seconds: int = 60
    disk_budget_bytes: int = DISK_BUDGET_BYTES
    log_ring_bytes: int = LOG_RING_BYTES
    activation_grace_seconds: int = 30
    test_mode: bool = False
    simulate_missing: frozenset[str] = field(default_factory=frozenset)
    occupy_port: bool = False
    seed_fault: str | None = None

    # derived locations -------------------------------------------------
    @property
    def lock_path(self) -> Path:
        return self.repo_root / "locks" / "emulator.lock.json"

    @property
    def downloads_dir(self) -> Path:
        return self.cache_dir / "downloads"

    @property
    def emulator_root(self) -> Path:
        return self.data_dir / "emulator"

    @property
    def runtime_venv(self) -> Path:
        return self.data_dir / "venv-runtime"

    @property
    def dev_venv(self) -> Path:
        return self.data_dir / "venv-dev"

    @property
    def receipt_path(self) -> Path:
        return self.data_dir / "install-receipt.json"

    @property
    def sandboxes_dir(self) -> Path:
        return self.state_dir / "sandboxes"

    @property
    def global_lock(self) -> Path:
        return self.state_dir / "lifecycle.lock"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.json"

    @property
    def tmp_dir(self) -> Path:
        return self.cache_dir / "tmp"

    @property
    def src_dir(self) -> Path:
        return self.repo_root / "src"

    @property
    def scenarios_dir(self) -> Path:
        return self.repo_root / "scenarios"

    # durations ---------------------------------------------------------
    @property
    def lease_seconds(self) -> int:
        return LEASE_MINUTES * self.minute_seconds

    @property
    def max_lifetime_seconds(self) -> int:
        return MAX_LIFETIME_MINUTES * self.minute_seconds

    @property
    def idle_seconds(self) -> int:
        return IDLE_MINUTES * self.minute_seconds

    def owned_locations(self) -> dict[str, Path]:
        locs = {
            "data": self.data_dir,
            "state": self.state_dir,
            "cache": self.cache_dir,
        }
        if self.runtime_dir is not None:
            locs["runtime"] = self.runtime_dir
        return locs


def _xdg(env, var: str, default: str) -> Path:
    value = env.get(var)
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / default


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    runtime_base = env.get("XDG_RUNTIME_DIR")
    runtime_dir = Path(runtime_base) / APP if runtime_base and os.path.isabs(runtime_base) else None
    settings = Settings(
        data_dir=_xdg(env, "XDG_DATA_HOME", ".local/share") / APP,
        state_dir=_xdg(env, "XDG_STATE_HOME", ".local/state") / APP,
        cache_dir=_xdg(env, "XDG_CACHE_HOME", ".cache") / APP,
        runtime_dir=runtime_dir,
    )
    if env.get("AWS_LAB_TEST_MODE") == "1":
        settings = _apply_test_config(settings, env.get("AWS_LAB_TEST_CONFIG", "{}"))
    return settings


def _apply_test_config(settings: Settings, raw: str) -> Settings:
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LabError("unsafe_configuration", "test configuration is not valid JSON") from exc
    if not isinstance(cfg, dict) or set(cfg) - _TEST_KEYS:
        raise LabError("unsafe_configuration", "test configuration has unknown keys", {"allowed": sorted(_TEST_KEYS)})
    changes: dict = {"test_mode": True}
    if "minute_seconds" in cfg:
        value = cfg["minute_seconds"]
        if not isinstance(value, int) or not 1 <= value <= 60:
            raise LabError("unsafe_configuration", "minute_seconds may only shorten time (1..60)")
        changes["minute_seconds"] = value
    if "disk_budget_bytes" in cfg:
        value = cfg["disk_budget_bytes"]
        if not isinstance(value, int) or not 0 <= value <= DISK_BUDGET_BYTES:
            raise LabError("unsafe_configuration", "disk budget may only be lowered")
        changes["disk_budget_bytes"] = value
    if "log_ring_bytes" in cfg:
        value = cfg["log_ring_bytes"]
        if not isinstance(value, int) or not 1024 <= value <= LOG_RING_BYTES:
            raise LabError("unsafe_configuration", "log ring may only be lowered (>=1024)")
        changes["log_ring_bytes"] = value
    if "activation_grace_seconds" in cfg:
        value = cfg["activation_grace_seconds"]
        if not isinstance(value, int) or not 1 <= value <= 30:
            raise LabError("unsafe_configuration", "activation grace may only be shortened (1..30)")
        changes["activation_grace_seconds"] = value
    if "simulate_missing" in cfg:
        value = cfg["simulate_missing"]
        if not isinstance(value, list) or set(value) - _MISSING_CAPS:
            raise LabError("unsafe_configuration", "simulate_missing accepts only bwrap, systemd, cgroup")
        changes["simulate_missing"] = frozenset(value)
    if "occupy_port" in cfg:
        if not isinstance(cfg["occupy_port"], bool):
            raise LabError("unsafe_configuration", "occupy_port must be boolean")
        changes["occupy_port"] = cfg["occupy_port"]
    if "seed_fault" in cfg:
        if cfg["seed_fault"] not in _SEED_FAULTS:
            raise LabError("unsafe_configuration", "unknown seed fault", {"allowed": sorted(_SEED_FAULTS)})
        changes["seed_fault"] = cfg["seed_fault"]
    return replace(settings, **changes)
