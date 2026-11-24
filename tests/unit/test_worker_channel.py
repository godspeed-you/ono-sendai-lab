"""Worker channel framing and request binding (owner + generation)."""

import json
import socket
import threading
import types

import pytest

from ono_sendai_lab import ipc
from ono_sendai_lab.errors import LabError
from ono_sendai_lab.supervisor import RingLog, Supervisor, sanitize_emulator_line


def _supervisor(tmp_path, monkeypatch, test_hooks=False):
    import ono_sendai_lab.supervisor as sup_mod

    monkeypatch.setattr(sup_mod, "CTL", tmp_path)
    monkeypatch.setattr(sup_mod, "LOGS", tmp_path)
    args = types.SimpleNamespace(
        generation="g" * 32,
        lease_deadline=1e12,
        max_deadline=2e12,
        startup_timeout=30,
        activation_grace=30,
        log_ring_bytes=4096,
        test_hooks=test_hooks,
        region="us-west-2",
        occupy_port=False,
        seed_fault=None,
        idle_seconds=900,
    )
    return Supervisor(args)


def _roundtrip(sup, request):
    a, b = socket.socketpair()
    t = threading.Thread(target=sup._handle, args=(b,))
    t.start()
    ipc.send_frame(a, request, ipc.MAX_REQUEST)
    resp = ipc.recv_frame(a, ipc.MAX_RESPONSE)
    t.join()
    a.close()
    b.close()
    return resp


def test_stale_generation_is_rejected(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path, monkeypatch)
    resp = _roundtrip(sup, {"v": 1, "generation": "x" * 32, "op": "ping", "params": {}})
    assert not resp["ok"] and resp["error"]["code"] == "stale_generation"
    resp = _roundtrip(sup, {"v": 1, "generation": "g" * 32, "op": "ping", "params": {}})
    assert resp["ok"] and resp["result"]["generation"] == "g" * 32


def test_unknown_and_private_ops_rejected(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path, monkeypatch)
    for op in ("shell", "_handle", "__init__", "finish", "exec"):
        resp = _roundtrip(sup, {"v": 1, "generation": "g" * 32, "op": op, "params": {}})
        assert not resp["ok"] and resp["error"]["code"] == "operation_not_allowed"


def test_probe_requires_test_mode(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path, monkeypatch, test_hooks=False)
    resp = _roundtrip(sup, {"v": 1, "generation": "g" * 32, "op": "probe", "params": {"checks": []}})
    assert resp["error"]["code"] == "operation_not_allowed"


def test_aws_requires_activation(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path, monkeypatch)
    resp = _roundtrip(sup, {"v": 1, "generation": "g" * 32, "op": "aws", "params": {"tokens": ["s3", "list-buckets"]}})
    assert resp["error"]["code"] == "not_ready"


def test_lease_cannot_pass_absolute_lifetime(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path, monkeypatch)
    resp = _roundtrip(sup, {"v": 1, "generation": "g" * 32, "op": "activate", "params": {"lease_deadline": 3e12}})
    assert resp["error"]["code"] == "lease_cap_exceeded"


def test_oversized_request_refused(tmp_path, monkeypatch):
    sup = _supervisor(tmp_path, monkeypatch)
    a, b = socket.socketpair()
    t = threading.Thread(target=sup._handle, args=(b,))
    t.start()
    a.sendall((ipc.MAX_REQUEST + 1).to_bytes(4, "big"))
    resp = ipc.recv_frame(a, ipc.MAX_RESPONSE)
    t.join()
    assert resp["error"]["code"] == "invalid_argument"
    with pytest.raises(ValueError):
        ipc.send_frame(a, {"x": "y" * ipc.MAX_REQUEST}, ipc.MAX_REQUEST)


def test_client_maps_missing_socket(tmp_path):
    with pytest.raises(LabError) as exc:
        ipc.call(str(tmp_path / "none.sock"), "g", "ping")
    assert exc.value.code == "transport_error"


def test_emulator_log_lines_are_sanitized():
    line = json.dumps(
        {
            "timestamp": "t",
            "level": "INFO",
            "fields": {
                "message": "request authentication enabled",
                "credentials_file": "/home/lab/x",
                "access_key": "EMULATOR0123456789AB",
            },
            "target": "x",
        }
    )
    out = sanitize_emulator_line(line)
    assert "EMULATOR0123456789AB" not in out and "credentials_file" not in out
    assert json.loads(out)["dropped_fields"] == 2
    assert "unparsed_bytes" in sanitize_emulator_line("not json EMULATOR0123456789AB")


def test_ring_log_is_bounded(tmp_path):
    log = RingLog(tmp_path / "lab.log", 2048)
    for i in range(500):
        log.write("LAB", f"line {i} " + "x" * 50)
    assert (tmp_path / "lab.log").stat().st_size <= 2048
    assert (tmp_path / "lab.log.1").stat().st_size <= 2048
    assert not (tmp_path / "lab.log.2").exists()
