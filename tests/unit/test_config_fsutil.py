import json
import os
import stat

import pytest

from ono_sendai_lab import config
from ono_sendai_lab.errors import LabError
from ono_sendai_lab.fsutil import atomic_write_json, ensure_private_dir, read_json_nofollow, remove_tree, tree_size


def test_production_defaults(settings):
    assert not settings.test_mode
    assert settings.lease_seconds == 30 * 60
    assert settings.max_lifetime_seconds == 120 * 60
    assert settings.idle_seconds == 15 * 60
    assert settings.disk_budget_bytes == 5_000_000_000
    assert config.MEMORY_MAX_BYTES == 1_073_741_824


def test_test_config_ignored_without_test_mode(tmp_path):
    s = config.load_settings({"AWS_LAB_TEST_CONFIG": json.dumps({"minute_seconds": 1}), "XDG_STATE_HOME": str(tmp_path)})
    assert s.minute_seconds == 60 and not s.test_mode


def test_test_config_can_only_shorten(tmp_path):
    env = {"AWS_LAB_TEST_MODE": "1", "XDG_STATE_HOME": str(tmp_path)}
    s = config.load_settings({**env, "AWS_LAB_TEST_CONFIG": json.dumps({"minute_seconds": 1, "disk_budget_bytes": 10})})
    assert s.test_mode and s.minute_seconds == 1 and s.disk_budget_bytes == 10
    for bad in (
        {"minute_seconds": 61},
        {"minute_seconds": 0},
        {"disk_budget_bytes": 6_000_000_000},
        {"log_ring_bytes": 10_000_000},
        {"simulate_missing": ["network"]},
        {"seed_fault": "anything"},
        {"memory_max": 1},
        {"unknown": True},
    ):
        with pytest.raises(LabError) as exc:
            config.load_settings({**env, "AWS_LAB_TEST_CONFIG": json.dumps(bad)})
        assert exc.value.code == "unsafe_configuration"


def test_private_dir_refuses_symlink(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "l"
    link.symlink_to(target)
    with pytest.raises(LabError):
        ensure_private_dir(link)


def test_private_dir_tightens_mode(tmp_path):
    d = tmp_path / "d"
    d.mkdir(mode=0o755)
    ensure_private_dir(d)
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


def test_atomic_write_and_nofollow(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1})
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert read_json_nofollow(p) == {"a": 1}
    link = tmp_path / "y.json"
    link.symlink_to(p)
    with pytest.raises(OSError):
        read_json_nofollow(link)


def test_remove_tree_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f").write_text("y")
    (tree / "link").symlink_to(outside)
    assert tree_size(tree) > 0
    remove_tree(tree)
    assert not tree.exists()
    assert (outside / "keep").exists()


def test_settings_honour_the_given_environment(tmp_path):
    """Regression: load_settings(env) once read os.environ and let unit tests
    write records into the operator's real state directory."""
    env = {"XDG_DATA_HOME": str(tmp_path / "d"), "XDG_STATE_HOME": str(tmp_path / "s"), "XDG_CACHE_HOME": str(tmp_path / "c")}
    s = config.load_settings(env)
    assert s.data_dir == tmp_path / "d" / "ono-sendai-lab"
    assert s.state_dir == tmp_path / "s" / "ono-sendai-lab"
    assert s.cache_dir == tmp_path / "c" / "ono-sendai-lab"
    assert s.runtime_dir is None


def test_unit_settings_fixture_is_isolated(settings, tmp_path):
    assert tmp_path in settings.state_dir.parents and tmp_path in settings.data_dir.parents
