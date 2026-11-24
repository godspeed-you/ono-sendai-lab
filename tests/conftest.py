"""Shared test helpers. Unit tests fake only external boundaries."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ono_sendai_lab.config import load_settings  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    """Settings rooted in a private temporary tree (no real lab state)."""
    env = {
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
    }
    (tmp_path / "run").mkdir(mode=0o700)
    return load_settings(env)


@pytest.fixture
def repo_root():
    return ROOT


def with_changes(settings, **changes):
    return replace(settings, **changes)


os.environ.pop("AWS_LAB_TEST_MODE", None)
