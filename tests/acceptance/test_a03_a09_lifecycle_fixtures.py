"""A03-A09: real start, limits, fixtures, mutation, seed semantics, reset."""

import hashlib
import json
import os
import socket
import time
from pathlib import Path

import pytest
from labharness import ROOT, RUN_DIR, active_lab_units, unit_props

LOCK = json.loads((ROOT / "locks" / "emulator.lock.json").read_text())
MINIMAL_OPS = json.loads((ROOT / "scenarios" / "aws-minimal.json").read_text())["requires"]["operations"]
EMPTY_OPS = json.loads((ROOT / "scenarios" / "aws-empty.json").read_text())["requires"]["operations"]


@pytest.mark.row("A03")
def test_A03_prebuilt_emulator_starts_verified(lab):
    res = lab.ok("create", "--name", "a03")
    assert res["state"] == "READY" and res["fixture"]["state"] == "EMPTY"
    startup = lab.runtime("a03")["startup"]
    assert startup["auth"] == {"mode": "sigv4", "unsigned_request_status": 403}
    assert startup["telemetry"] == "disabled" and startup["tls"] == "verified-with-run-ca"
    assert startup["identity"] == LOCK["qualified_identity"]
    assert startup["region"] == "us-west-2" and startup["zones"] >= 1
    lab.observe_ops(["ec2 describe-availability-zones"], "ok")
    # The running process is the pinned prebuilt binary (hash of /proc/PID/exe).
    pid = lab.emulator_pid("a03")
    digest = hashlib.sha256()
    with open(f"/proc/{pid}/exe", "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    assert digest.hexdigest() == LOCK["executable"]["sha256"]
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    assert b"--auth" in cmdline and cmdline[cmdline.index(b"--auth") + 1] == b"on"
    assert cmdline[cmdline.index(b"--telemetry") + 1] == b"off"
    assert cmdline[cmdline.index(b"--bind") + 1] == b"127.0.0.1"
    ident = lab.ok("aws", "a03", "--", "sts", "get-caller-identity")["response"]
    assert ident["Account"] == "000000000000"
    lab.observe_ops(["sts get-caller-identity"], "ok")
    logs = "\n".join(lab.ok("logs", "a03", "--tail", "200")["lines"])
    assert "telemetry: disabled" in logs and "request authentication enabled" in logs
    creds = lab.ok("credentials", "a03")
    assert creds["endpoint"]["reachable_from_host"] is False
    port = int(creds["endpoint"]["url"].rsplit(":", 1)[1])
    with socket.socket() as s:
        s.settimeout(1)
        assert s.connect_ex(("127.0.0.1", port)) != 0, "emulator endpoint must not be reachable from the host network"
    lab.ok("destroy", "a03")


@pytest.mark.row("A04")
def test_A04_effective_limits_and_single_slot(lab):
    lab.ok("create", "--name", "a04")
    rt = lab.runtime("a04")
    base = Path(f"/sys/fs/cgroup{rt['control_group']}")
    assert (base / "cpu.max").read_text().split() == ["100000", "100000"]
    assert (base / "memory.max").read_text().strip() == "1073741824"
    assert (base / "memory.swap.max").read_text().strip() == "0"
    assert (base / "memory.zswap.max").read_text().strip() == "0"
    assert (base / "pids.max").read_text().strip() == "512"
    assert int((base / "memory.swap.current").read_text()) == 0
    pid = lab.emulator_pid("a04")
    assert (
        Path(f"/proc/{pid}/cgroup").read_text().strip().endswith(".service") or "ono-sendai-lab" in Path(f"/proc/{pid}/cgroup").read_text()
    )
    props = unit_props(rt["unit"])
    assert props["ActiveState"] == "active" and props["InvocationID"] == rt["invocation_id"]
    err = lab.fail("slot_busy", 4, "create", "--name", "a04-second")
    assert err["details"]["active"] == ["a04"]
    assert len(active_lab_units()) == 1
    lab.ok("destroy", "a04")


@pytest.mark.row("A04")
def test_A04_disk_admission_refuses_over_budget(lab):
    err = lab.fail("disk_budget_exceeded", 4, "create", "--name", "a04-disk", config={"disk_budget_bytes": 1000})
    assert err["details"]["budget_bytes"] == 1000
    assert lab.ok("status")["sandboxes"] == [] and active_lab_units() == []


@pytest.mark.row("A04")
def test_A04_logs_are_bounded(lab):
    lab.ok("create", "--name", "a04-logs", config={"log_ring_bytes": 2048})
    for _ in range(30):
        lab.ok("aws", "a04-logs", "--", "s3", "list-buckets")
    logs_dir = Path(lab.record("a04-logs")["sandbox_id"])
    directory = RUN_DIR / "state" / "ono-sendai-lab" / "sandboxes" / logs_dir / "logs"
    assert (directory / "lab.log").stat().st_size <= 2048
    assert (directory / "lab.log.1").stat().st_size <= 2048
    assert not (directory / "lab.log.2").exists()
    default = lab.ok("logs", "a04-logs")
    assert default["returned"] <= 50
    full = lab.ok("logs", "a04-logs", "--tail", "200")
    assert full["returned"] <= 200 and full["truncated"] == (full["available"] > full["returned"])
    lab.fail("invalid_argument", 2, "logs", "a04-logs", "--tail", "201")
    lab.fail("invalid_argument", 2, "logs", "a04-logs", "--tail", "0")
    lab.ok("destroy", "a04-logs")


@pytest.mark.row("A05")
def test_A05_aws_empty_observes_baseline_without_creating(lab):
    created = lab.ok("create", "--name", "a05")
    baseline = created["baseline"]
    assert baseline["identity"]["account"] == "000000000000"
    assert baseline["lab_tagged"] == {"vpcs": 0, "subnets": 0, "security_groups": 0}
    res = lab.ok("seed", "a05", "--scenario", "aws-empty")
    assert res["fixture_state"] == "VERIFIED" and res["mapping"] == {} and res["steps"] == []
    assert res["verification"]["observed"]["s3_buckets_beyond_baseline"] == []
    buckets = lab.ok("aws", "a05", "--", "s3", "list-buckets")["response"]["Buckets"]
    assert sorted(b["Name"] for b in buckets) == baseline["s3_buckets"]
    vpcs = lab.ok("aws", "a05", "--", "ec2", "describe-vpcs")["response"]["Vpcs"]
    assert sorted(v["VpcId"] for v in vpcs) == baseline["ec2_vpcs"]
    default_vpcs = [v["VpcId"] for v in vpcs if v.get("IsDefault")]
    assert default_vpcs == baseline["ec2_default_vpcs"]
    status = lab.ok("status", "a05")
    assert status["fixture"]["state"] == "VERIFIED" and status["fixture"]["scenario"] == "aws-empty"
    listing = lab.ok("status")
    assert listing["active_generations"] == 1 and listing["sandboxes"][0]["name"] == "a05"
    lab.observe_ops(EMPTY_OPS, "ok", "aws-empty")
    lab.ok("destroy", "a05")


def _minimal(lab, name, **kw):
    lab.ok("create", "--name", name, **kw)
    res = lab.ok("seed", name, "--scenario", "aws-minimal")
    assert res["fixture_state"] == "VERIFIED"
    return res


@pytest.mark.row("A06")
def test_A06_aws_minimal_topology_independently_verified(lab):
    res = _minimal(lab, "a06")
    m = res["mapping"]
    assert set(m) == {"bucket", "vpc", "subnet", "security_group"}
    assert res["verification"]["assertions"] >= 20
    assert res["verification"]["observed"]["generation_owned_counts"] == {"vpcs": 1, "subnets": 1, "security_groups": 1}
    vpc = lab.ok("aws", "a06", "--", "ec2", "describe-vpcs", "--vpc-ids", m["vpc"])["response"]["Vpcs"][0]
    assert vpc["CidrBlock"] == "10.42.0.0/16" and not vpc["IsDefault"]
    assert {"Key": "ono-sendai-lab:scenario", "Value": "aws-minimal"} in vpc["Tags"]
    sub = lab.ok("aws", "a06", "--", "ec2", "describe-subnets", "--subnet-ids", m["subnet"])["response"]["Subnets"][0]
    assert sub["CidrBlock"] == "10.42.1.0/24" and sub["VpcId"] == m["vpc"]
    grp = lab.ok("aws", "a06", "--", "ec2", "describe-security-groups", "--group-ids", m["security_group"])["response"]["SecurityGroups"][0]
    assert grp["VpcId"] == m["vpc"] and grp["GroupName"] == "osl-aws-minimal-sg"
    buckets = [b["Name"] for b in lab.ok("aws", "a06", "--", "s3", "list-buckets")["response"]["Buckets"]]
    assert m["bucket"] in buckets
    lab.observe_ops(MINIMAL_OPS, "ok", "aws-minimal")
    lab.ok("destroy", "a06")


@pytest.mark.row("A07")
def test_A07_direct_operations_observe_fixture_and_mutate(lab):
    res = _minimal(lab, "a07")
    m = res["mapping"]
    found = lab.ok("aws", "a07", "--", "ec2", "describe-vpcs", "--filters", "Name=tag:ono-sendai-lab:scenario,Values=aws-minimal")
    assert [v["VpcId"] for v in found["response"]["Vpcs"]] == [m["vpc"]]
    assert found["fixture_state"] == "VERIFIED" and found["mutating"] is False
    by_vpc = lab.ok("aws", "a07", "--", "ec2", "describe-subnets", "--filters", f"Name=vpc-id,Values={m['vpc']}")
    assert [s["SubnetId"] for s in by_vpc["response"]["Subnets"]] == [m["subnet"]]
    extra = f"a07-extra-{os.getpid()}"
    made = lab.ok("aws", "a07", "--", "s3", "create-bucket", "--bucket", extra)
    assert made["mutating"] is True and made["fixture_state"] == "DIRTY"
    assert extra in [b["Name"] for b in lab.ok("aws", "a07", "--", "s3", "list-buckets")["response"]["Buckets"]]
    lab.ok("aws", "a07", "--", "s3", "delete-bucket", "--bucket", extra)
    assert extra not in [b["Name"] for b in lab.ok("aws", "a07", "--", "s3", "list-buckets")["response"]["Buckets"]]
    err = lab.fail("aws_error", 5, "aws", "a07", "--", "s3", "delete-bucket", "--bucket", extra)
    assert err["details"]["aws_code"] == "NoSuchBucket"
    assert lab.ok("status", "a07")["fixture"]["state"] == "DIRTY"
    lab.observe_ops(["s3 create-bucket", "s3 delete-bucket", "s3 list-buckets", "ec2 describe-vpcs", "ec2 describe-subnets"], "ok")
    lab.ok("destroy", "a07")


def _counts(lab, name):
    buckets = len(lab.ok("aws", name, "--", "s3", "list-buckets")["response"]["Buckets"])
    vpcs = len(lab.ok("aws", name, "--", "ec2", "describe-vpcs")["response"]["Vpcs"])
    subnets = len(lab.ok("aws", name, "--", "ec2", "describe-subnets")["response"]["Subnets"])
    groups = len(lab.ok("aws", name, "--", "ec2", "describe-security-groups")["response"]["SecurityGroups"])
    return buckets, vpcs, subnets, groups


@pytest.mark.row("A08")
def test_A08_duplicate_seed_refused_without_mutation(lab):
    _minimal(lab, "a08")
    before = _counts(lab, "a08")
    err = lab.fail("fixture_state_conflict", 3, "seed", "a08", "--scenario", "aws-minimal")
    assert "reset" in err["details"]["remedy"]
    assert _counts(lab, "a08") == before
    lab.ok("destroy", "a08")


@pytest.mark.row("A08")
def test_A08_partial_seed_is_dirty_and_reset_rebuilds(lab):
    lab.ok("create", "--name", "a08p", config={"seed_fault": "subnet-outside-vpc"})
    err = lab.fail("seed_failed", 5, "seed", "a08p", "--scenario", "aws-minimal", config={"seed_fault": "subnet-outside-vpc"})
    partial = err["details"]["partial_mapping"]
    assert set(partial) == {"bucket", "vpc"} and err["details"]["failed"]["logical_id"] == "subnet"
    assert err["details"]["failed"]["error"]["details"]["aws_code"] == "InvalidSubnet.Range"
    status = lab.ok("status", "a08p")
    assert status["fixture"]["state"] == "DIRTY" and status["fixture"]["failed_operation"]["logical_id"] == "subnet"
    # No pretend rollback: the partially created bucket is still observable.
    assert partial["bucket"] in [b["Name"] for b in lab.ok("aws", "a08p", "--", "s3", "list-buckets")["response"]["Buckets"]]
    lab.fail("fixture_state_conflict", 3, "seed", "a08p", "--scenario", "aws-minimal")
    old_gen = status["generation"]["id"]
    res = lab.ok("reset", "a08p", "--scenario", "aws-minimal")
    assert res["previous_generation"] == old_gen and res["generation"]["id"] != old_gen
    assert res["fixture"]["state"] == "VERIFIED" and res["seed"]["verification"]["passed"] is True
    names = [b["Name"] for b in lab.ok("aws", "a08p", "--", "s3", "list-buckets")["response"]["Buckets"]]
    assert partial["bucket"] not in names and res["seed"]["mapping"]["bucket"] in names
    lab.ok("destroy", "a08p")


@pytest.mark.row("A09")
def test_A09_reset_rotates_and_invalidates(lab):
    seeded = _minimal(lab, "a09")
    c1 = lab.ok("credentials", "a09")
    old_ctl = Path(lab.runtime("a09")["ctl_dir"])
    res = lab.ok("reset", "a09")
    assert res["fixture"]["state"] == "EMPTY" and res["previous_generation"] == c1["generation"]
    c2 = lab.ok("credentials", "a09")
    assert c2["generation"] != c1["generation"]
    assert c2["tls"]["ca_sha256"] != c1["tls"]["ca_sha256"]
    assert c2["access_key_fingerprint"] != c1["access_key_fingerprint"]
    assert not old_ctl.exists()
    lab.fail("stale_generation", 3, "aws", "a09", "--generation", c1["generation"], "--", "s3", "list-buckets")
    lab.fail("stale_generation", 3, "credentials", "a09", "--generation", c1["generation"])
    lab.fail("stale_generation", 3, "extend", "a09", "--minutes", "1", "--generation", c1["generation"])
    buckets = [b["Name"] for b in lab.ok("aws", "a09", "--", "s3", "list-buckets")["response"]["Buckets"]]
    assert seeded["mapping"]["bucket"] not in buckets
    tagged = lab.ok("aws", "a09", "--", "ec2", "describe-vpcs", "--filters", "Name=tag-key,Values=ono-sendai-lab:scenario")
    assert tagged["response"]["Vpcs"] == []
    assert lab.ok("status", "a09")["history"][-1]["final_state"] == "STOPPED"
    lab.ok("destroy", "a09")
    assert time.time() and not active_lab_units()
