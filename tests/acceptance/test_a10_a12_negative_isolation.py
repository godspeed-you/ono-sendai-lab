"""A10 negative client paths, A11 containment, A12 death and startup failure."""

import errno
import os
import signal
import socket
import subprocess
from pathlib import Path

import pytest
from labharness import ROOT, RUN_DIR, Listener, active_lab_units, unit_props, wait_until

TEST = {}  # test mode with default durations: enables read-only probes only


def probe(lab, name, *checks):
    res = lab.ok("status", name)  # reconcile first; probes go through the worker channel
    assert res["state"] == "READY"
    from ono_sendai_lab import ipc

    rt = lab.runtime(name)
    out = ipc.call(str(Path(rt["ctl_dir"]) / "worker.sock"), rt["generation_id"], "probe", {"checks": list(checks)}, timeout=60)
    return [r["outcome"] for r in out["results"]]


@pytest.mark.row("A10")
def test_A10_negative_paths_fail_without_fallback(lab):
    sentinel = Listener()
    try:
        env = {"HTTPS_PROXY": f"http://127.0.0.1:{sentinel.port}", "AWS_ENDPOINT_URL": f"https://127.0.0.1:{sentinel.port}"}
        lab.ok("create", "--name", "a10", config=TEST, env=env)
        wrong_keys, wrong_ca, unreachable = probe(
            lab, "a10", {"kind": "wrong_credentials"}, {"kind": "wrong_ca"}, {"kind": "unreachable_endpoint"}
        )
        assert wrong_keys == {"ok": False, "code": "aws_error", "aws_code": "InvalidClientTokenId", "reason": None}
        assert wrong_ca["ok"] is False and wrong_ca["code"] == "transport_error" and wrong_ca["reason"] == "tls_verification_failed"
        assert unreachable["ok"] is False and unreachable["reason"] == "endpoint_unreachable"
        for args, code in [
            (["s3", "create-bucket", "--bucket", "Not_Valid"], "invalid_argument"),
            (["s3", "create-bucket", "--bucket", "file://etc-passwd"], "operation_not_allowed"),
            (["s3", "list-buckets", "--region", "us-east-1"], "operation_not_allowed"),
            (["s3", "list-buckets", "--profile", "default"], "operation_not_allowed"),
            (["s3", "list-buckets", "--ca-bundle", "/tmp/x"], "operation_not_allowed"),
            (["s3", "list-buckets", "--no-verify-ssl"], "operation_not_allowed"),
            (["ec2", "run-instances"], "operation_not_allowed"),
            (["ec2", "describe-vpcs", "--vpc-ids", "vpc-;id"], "invalid_argument"),
        ]:
            lab.fail(code, 2, "aws", "a10", "--", *args, env=env)
        ident = lab.ok("aws", "a10", "--", "sts", "get-caller-identity", env=env)["response"]
        assert ident["Account"] == "000000000000"
        assert sentinel.accepted == 0, "a request fell back to an ambient endpoint or proxy"
    finally:
        sentinel.close()
    lab.ok("destroy", "a10")


def _host_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # UDP connect sends nothing; it only selects a route
        return s.getsockname()[0]


@pytest.mark.row("A11")
def test_A11_namespace_cannot_reach_host_listeners(lab):
    loop = Listener("127.0.0.1")
    wide = Listener("0.0.0.0")
    abstract = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    abstract_name = f"osl-sentinel-{os.getpid()}"
    abstract.bind("\0" + abstract_name)
    abstract.listen(1)
    try:
        lab.ok("create", "--name", "a11", config=TEST)
        host_ip = _host_ip()
        r_loop, r_wide, r_abs, report = probe(
            lab,
            "a11",
            {"kind": "tcp", "host": "127.0.0.1", "port": loop.port},
            {"kind": "tcp", "host": host_ip, "port": wide.port},
            {"kind": "abstract_unix", "name": abstract_name},
            {"kind": "selfcheck"},
        )
        assert r_loop.startswith("ConnectionRefusedError")
        assert r_wide.startswith("OSError") and str(errno.ENETUNREACH) in r_wide
        assert r_abs == "ConnectionRefusedError"
        assert report["ok"] and report["interfaces"] == ["lo"] and report["ipv4_routes"] == []
        assert loop.accepted == 0 and wide.accepted == 0
    finally:
        loop.close()
        wide.close()
        abstract.close()
    lab.ok("destroy", "a11")


@pytest.mark.row("A11")
def test_A11_ambient_poisoning_and_filesystem_sentinels(lab):
    sentinel = Listener()
    home = RUN_DIR / "sentinel-home"
    (home / ".aws").mkdir(parents=True, exist_ok=True)
    creds = home / ".aws" / "credentials"
    creds.write_text("[default]\naws_access_key_id = AKIASENTINEL0000000A\naws_secret_access_key = sentinel-not-a-secret\n")
    (home / ".aws" / "config").write_text(f"[default]\nregion = eu-central-1\nendpoint_url = https://127.0.0.1:{sentinel.port}\n")
    poison = {
        "HOME": str(home),
        "AWS_ACCESS_KEY_ID": "AKIASENTINEL0000000A",
        "AWS_SECRET_ACCESS_KEY": "sentinel-not-a-secret",
        "AWS_SESSION_TOKEN": "sentinel-token",
        "AWS_PROFILE": "sentinel",
        "AWS_REGION": "eu-central-1",
        "AWS_DEFAULT_REGION": "eu-central-1",
        "AWS_ENDPOINT_URL": f"https://127.0.0.1:{sentinel.port}",
        "AWS_CA_BUNDLE": str(creds),
        "AWS_CONFIG_FILE": str(home / ".aws" / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(creds),
        "HTTPS_PROXY": f"http://127.0.0.1:{sentinel.port}",
        "HTTP_PROXY": f"http://127.0.0.1:{sentinel.port}",
        "ALL_PROXY": f"http://127.0.0.1:{sentinel.port}",
        "https_proxy": f"http://127.0.0.1:{sentinel.port}",
    }
    try:
        lab.ok("create", "--name", "a11p", config=TEST, env=poison)
        ident = lab.ok("aws", "a11p", "--", "sts", "get-caller-identity", env=poison)["response"]
        assert ident["Account"] == "000000000000" and ident["Arn"].endswith(":user/emulator")
        assert lab.ok("credentials", "a11p", env=poison)["region"] == "us-west-2"
        lab.ok("aws", "a11p", "--", "s3", "list-buckets", env=poison)
        env_names, *paths = probe(
            lab,
            "a11p",
            {"kind": "env_names"},
            {"kind": "path_exists", "path": str(creds)},
            {"kind": "path_exists", "path": str(home)},
            {"kind": "path_exists", "path": str(ROOT)},
            {"kind": "path_exists", "path": os.environ["HOME"]},
            {"kind": "path_exists", "path": os.environ["XDG_RUNTIME_DIR"]},
            {"kind": "path_exists", "path": "/var/run/docker.sock"},
            {"kind": "path_exists", "path": "/etc/passwd"},
        )
        lab_aws_vars = {"AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_EC2_METADATA_DISABLED", "AWS_DISABLE_HOST_PREFIX_INJECTION"}
        assert {n for n in env_names if n.startswith("AWS_")} <= lab_aws_vars
        assert not [n for n in env_names if "proxy" in n.lower()]
        assert paths == [False] * len(paths)
        assert sentinel.accepted == 0
    finally:
        sentinel.close()
    lab.ok("destroy", "a11p", env=poison)


@pytest.mark.row("A12")
def test_A12_unexpected_death_detected_and_unrelated_process_untouched(lab):
    decoy = subprocess.Popen(["sleep", "300"])
    try:
        lab.ok("create", "--name", "a12")
        rt = lab.runtime("a12")
        os.kill(lab.emulator_pid("a12"), signal.SIGKILL)
        assert wait_until(lambda: unit_props(rt["unit"]).get("ActiveState") != "active", 20)
        status = lab.ok("status", "a12")
        assert status["state"] == "FAILED" and status["state_reason"] == "emulator-died"
        assert status["fixture"]["state"] == "UNKNOWN"
        lab.fail("not_ready", 3, "aws", "a12", "--", "s3", "list-buckets")
        assert not Path(rt["ctl_dir"]).exists()
        assert unit_props(rt["unit"]).get("LoadState") == "not-found"
        lab.ok("destroy", "a12")
        assert decoy.poll() is None, "an unrelated process was signalled"
    finally:
        decoy.kill()
        decoy.wait()


@pytest.mark.row("A12")
def test_A12_startup_failure_recognized_and_cleaned(lab):
    err = lab.fail("startup_failed", 5, "create", "--name", "a12s", config={"occupy_port": True})
    assert err["details"]["reason"] == "emulator-exited-during-startup"
    status = lab.ok("status", "a12s")
    assert status["state"] == "FAILED" and status["fixture"]["state"] == "UNKNOWN"
    assert active_lab_units() == []
    rec = lab.record("a12s")
    assert not (Path(os.environ["XDG_RUNTIME_DIR"]) / "ono-sendai-lab" / rec["sandbox_id"]).exists()
    logs = "\n".join(lab.ok("logs", "a12s", "--tail", "200")["lines"])
    assert "Address already in use" in logs
    lab.ok("destroy", "a12s")
