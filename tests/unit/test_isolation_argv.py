from pathlib import Path

import pytest

from ono_sendai_lab import isolation
from ono_sendai_lab.errors import LabError


def _argv(tmp_path):
    return isolation.bwrap_argv(
        "/usr/bin/bwrap",
        emulator_dir=tmp_path / "emu",
        venv_dir=tmp_path / "venv",
        src_dir=tmp_path / "src",
        ctl_dir=tmp_path / "ctl",
        logs_dir=tmp_path / "logs",
        command=["/bin/true"],
    )


def test_bwrap_flags_enforce_isolation(tmp_path):
    argv = _argv(tmp_path)
    for flag in ("--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session", "--clearenv"):
        assert flag in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--share-net" not in argv


def test_only_allowlisted_host_paths_are_mounted(tmp_path):
    argv = _argv(tmp_path)
    binds = []
    for i, token in enumerate(argv):
        if token in ("--bind", "--ro-bind", "--dev-bind"):
            binds.append((token, argv[i + 1], argv[i + 2]))
    sources = {src for _, src, _ in binds}
    assert sources == {
        "/usr",
        str(tmp_path / "src"),
        str(tmp_path / "emu"),
        str(tmp_path / "venv"),
        str(tmp_path / "ctl"),
        str(tmp_path / "logs"),
    }
    writable = {src for kind, src, _ in binds if kind == "--bind"}
    assert writable == {str(tmp_path / "ctl"), str(tmp_path / "logs")}
    assert not any(kind == "--dev-bind" for kind, _, _ in binds)
    assert all(not s.startswith(str(Path.home() / ".aws")) for s in sources)


def test_environment_is_explicit(tmp_path):
    argv = _argv(tmp_path)
    env = {argv[i + 1]: argv[i + 2] for i, t in enumerate(argv) if t == "--setenv"}
    assert env["EMULATOR_TELEMETRY"] == "off"
    assert env["HOME"] == "/home/lab"
    assert not any(k.lower().endswith("proxy") for k in env)
    assert not any(k.startswith("AWS_") and k != "AWS_EC2_METADATA_DISABLED" for k in env)


@pytest.mark.parametrize("bad", ["/a b", "/a/../b", "relative", "/a;b", "/a$b"])
def test_unsafe_paths_rejected(bad):
    with pytest.raises(LabError):
        isolation.safe_host_path(Path(bad))


def test_systemd_unit_limits(settings, tmp_path):
    argv = isolation.systemd_run_argv(
        settings,
        "ono-sendai-lab-aaaaaaaaaaaa-bbbbbbbbbbbb.service",
        "d",
        runtime_max_sec=7200,
        stderr_path=tmp_path / "e",
        command=["/bin/true"],
    )
    props = [argv[i + 1] for i, t in enumerate(argv) if t == "-p"]
    for expected in (
        "CPUQuota=100%",
        "MemoryMax=1073741824",
        "MemorySwapMax=0",
        "RuntimeMaxSec=7200",
        "KillMode=control-group",
        "OOMPolicy=stop",
    ):
        assert expected in props
    assert "--user" in argv and "--scope" not in argv


def test_verify_limits():
    good = {"cpu.max": "100000 100000", "memory.max": "1073741824", "memory.swap.max": "0", "memory.zswap.max": "0", "pids.max": "512"}
    assert isolation.verify_limits(good) == []
    assert isolation.verify_limits({**good, "cpu.max": "max 100000"})
    assert isolation.verify_limits({**good, "memory.max": "max"})
    assert isolation.verify_limits({**good, "memory.swap.max": "max"})
    assert isolation.verify_limits({**good, "pids.max": "max"})


def test_unit_names_are_strict():
    name = isolation.unit_name("a" * 32, "b" * 32)
    assert isolation.UNIT_RE.match(name)
    with pytest.raises(LabError):
        isolation.unit_name("../../x", "b" * 32)


def test_stop_without_invocation_requires_matching_description(settings, monkeypatch):
    from ono_sendai_lab import local_backend

    stopped = []
    props = {"LoadState": "loaded", "ActiveState": "active", "InvocationID": "abc", "Description": "something else"}
    monkeypatch.setattr(local_backend.isolation, "show_unit", lambda s, u: props)
    monkeypatch.setattr(local_backend.isolation, "stop_unit", lambda s, u: stopped.append(u))
    backend = local_backend.LocalBackend(settings)
    runtime = {
        "unit": "ono-sendai-lab-aaaaaaaaaaaa-bbbbbbbbbbbb.service",
        "description": "ono-sendai-lab sandbox a generation b",
        "invocation_id": None,
    }
    with pytest.raises(LabError):
        backend._stop_if_present(runtime)
    assert stopped == []
    props["Description"] = runtime["description"]
    assert backend._stop_if_present(runtime)["stopped"] is True
    runtime["invocation_id"] = "different"
    with pytest.raises(LabError):
        backend._stop_if_present(runtime)
