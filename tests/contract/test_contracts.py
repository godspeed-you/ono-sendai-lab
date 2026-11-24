"""Machine-readable contracts agree with the implementation (LAB-052, LAB-062, LAB-081)."""

import inspect
import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ono_sendai_lab import backend, errors, operations, registry, scenarios

ROOT = Path(__file__).resolve().parents[2]
C = ROOT / "docs" / "contracts"


def load(name):
    return json.loads((C / name).read_text())


SCHEMAS = [
    "cli-envelope.schema.json",
    "cli-results.schema.json",
    "scenario-manifest.schema.json",
    "dependency-lock.schema.json",
    "operation-compatibility.schema.json",
]


@pytest.mark.parametrize("name", SCHEMAS)
def test_schemas_are_valid(name):
    Draft202012Validator.check_schema(load(name))


def envelope_validator():
    env, res = load("cli-envelope.schema.json"), load("cli-results.schema.json")
    reg = Registry().with_resources([(env["$id"], Resource.from_contents(env)), (res["$id"], Resource.from_contents(res))])
    return Draft202012Validator(env, registry=reg)


def test_error_codes_contract_matches_code():
    doc = load("error-codes.json")
    assert doc["codes"] == {k: {"exit_code": v[0], "retryable": v[1]} for k, v in errors.ERROR_CODES.items()}
    enum = load("cli-envelope.schema.json")["$defs"]["error"]["properties"]["code"]["enum"]
    assert sorted(enum) == sorted(errors.ERROR_CODES)
    assert set(doc["exit_classes"]) == {"0", "2", "3", "4", "5", "6"}


def test_operations_contract_matches_code():
    assert load("aws-operations.json") == json.loads(json.dumps(operations.contract()))


def test_lifecycle_contract_matches_code():
    doc = load("lifecycle-states.json")
    assert doc["process_states"] == list(registry.PROCESS_STATES)
    assert doc["fixture_states"] == list(registry.FIXTURE_STATES)
    assert doc["active_states"] == list(registry.ACTIVE_STATES)
    assert doc["process_transitions"] == {str(k): sorted(v) for k, v in registry.TRANSITIONS.items()}
    assert doc["fixture_transitions"] == {str(k): sorted(v) for k, v in registry.FIXTURE_TRANSITIONS.items()}
    assert ["STARTING", "READY", "STOPPING", "STOPPED", "FAILED", "EXPIRED"] == doc["process_states"]
    assert ["EMPTY", "SEEDING", "VERIFIED", "DIRTY", "UNKNOWN"] == doc["fixture_states"]


@pytest.mark.parametrize("sid", scenarios.SUPPORTED)
def test_scenario_manifests_follow_schema(sid):
    manifest = json.loads((ROOT / "scenarios" / f"{sid}.json").read_text())
    Draft202012Validator(load("scenario-manifest.schema.json")).validate(manifest)
    scenarios.load_manifest(ROOT / "scenarios", sid)
    known = {f"{s} {o}" for (s, o) in operations.OPERATIONS}
    assert set(manifest["requires"]["operations"]) <= known


def test_minimal_manifest_is_the_specified_topology():
    m = scenarios.load_manifest(ROOT / "scenarios", "aws-minimal")
    by = {r["logical_id"]: r for r in m["resources"]}
    assert [r["type"] for r in m["resources"]] == ["s3-bucket", "ec2-vpc", "ec2-subnet", "ec2-security-group"]
    assert by["vpc"]["cidr"] == "10.42.0.0/16" and by["subnet"]["cidr"] == "10.42.1.0/24"
    assert {(r["from"], r["to"]) for r in m["relations"]} == {("subnet", "vpc"), ("security_group", "vpc")}
    assert scenarios.load_manifest(ROOT / "scenarios", "aws-empty")["resources"] == []


def test_dependency_lock_follows_schema_and_pins_agree():
    lock = json.loads((ROOT / "locks" / "emulator.lock.json").read_text())
    Draft202012Validator(load("dependency-lock.schema.json")).validate(lock)
    runtime = (ROOT / "locks" / "requirements-runtime.txt").read_text()
    dev = (ROOT / "locks" / "requirements-dev.txt").read_text()
    pins = [line.split(" ")[0] for line in runtime.splitlines() if "==" in line and not line.startswith("#")]
    for pin in pins:
        assert pin in dev, f"dev lock must carry the runtime pin {pin}"
    assert runtime.count("--hash=sha256:") >= len(pins)
    pyproject = (ROOT / "pyproject.toml").read_text()
    botocore = next(p for p in pins if p.startswith("botocore=="))
    assert f'"{botocore}"' in pyproject


def test_compatibility_manifest_is_consistent():
    doc = load("operation-compatibility.json")
    Draft202012Validator(load("operation-compatibility.schema.json")).validate(doc)
    lock = json.loads((ROOT / "locks" / "emulator.lock.json").read_text())
    assert doc["emulator_version"] == lock["version"]
    pin = next(line for line in (ROOT / "locks" / "requirements-runtime.txt").read_text().splitlines() if line.startswith("botocore=="))
    assert doc["client"] == {"name": "botocore", "version": pin.split("==")[1].split()[0]}
    listed = {f"{o['service']} {o['operation']}": o for o in doc["operations"]}
    assert set(listed) == {f"{s} {o}" for (s, o) in operations.OPERATIONS}
    for name, entry in listed.items():
        assert entry["qualification_status"] == "verified", f"{name} is mandatory and must be directly verified"
        assert all(ref.startswith("tests/acceptance::test_A") for ref in entry["test_reference"])


def test_backend_contract_matches_code():
    doc = load("backend-contract.json")
    assert doc["implemented_backends"] == list(backend.SUPPORTED_BACKENDS)
    assert doc["documented_future_backends"] == list(backend.DOCUMENTED_FUTURE_BACKENDS)
    abstract = {name for name, member in inspect.getmembers(backend.Backend) if getattr(member, "__isabstractmethod__", False)}
    assert abstract == set(doc["logical_operations"])


@pytest.mark.parametrize(
    "args",
    [
        ["--version"],
        ["status"],
        ["scenarios"],
        ["status", "missing"],
        ["create", "--name", "x", "--backend", "remote-vm"],
        ["aws", "x", "--", "iam", "list-users"],
    ],
)
def test_offline_cli_documents_validate(tmp_path, args):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "s"),
        "XDG_DATA_HOME": str(tmp_path / "d"),
        "XDG_CACHE_HOME": str(tmp_path / "c"),
    }
    out = subprocess.run([str(ROOT / "bin" / "aws-lab"), "--json", *args], capture_output=True, text=True, env=env, timeout=60)
    doc = json.loads(out.stdout)
    errs = list(envelope_validator().iter_errors(doc))
    assert not errs, [e.message for e in errs]


@pytest.mark.parametrize("python_version", ["3.12", "3.13", "3.14"])
def test_locks_are_closed_for_every_supported_python(python_version):
    """Regression: referencing needs typing-extensions below 3.13; the lock was
    resolved on 3.14 and the CI gate on 3.12 failed at import time."""
    from importlib import metadata

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    for lock_name in ("requirements-runtime.txt", "requirements-dev.txt"):
        text = (ROOT / "locks" / lock_name).read_text()
        locked = {canonicalize_name(line.split("==")[0]) for line in text.splitlines() if "==" in line and not line.startswith("#")}
        env = {"python_version": python_version, "python_full_version": python_version + ".0", "extra": ""}
        for name in sorted(locked):
            try:
                requires = metadata.requires(name) or []
            except metadata.PackageNotFoundError:
                continue
            for spec in requires:
                req = Requirement(spec)
                if req.marker is None or req.marker.evaluate(env):
                    assert canonicalize_name(req.name) in locked, f"{lock_name}: {name} needs {req.name} on Python {python_version}"
