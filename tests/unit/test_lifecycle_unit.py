"""Lifecycle semantics against a fake backend (the backend is the external boundary)."""

import time

import pytest

from ono_sendai_lab import lifecycle
from ono_sendai_lab.backend import Backend, RuntimeObservation
from ono_sendai_lab.errors import LabError


class FakeBackend(Backend):
    name = "local"

    def __init__(self):
        self.alive = {}
        self.terminal = {}
        self.fail_start = None
        self.seed_ok = True
        self.started = []
        self.stopped = []

    def preflight(self):
        return {"containment": {"ok": True}, "resource_controls": {"limits": {"cpu.max": "100000 100000"}}}

    def prepare(self, spec):
        return {"backend": "local", "unit": f"u-{spec.generation_id}", "generation_id": spec.generation_id}

    def start(self, spec, runtime, logs_dir):
        self.alive[spec.generation_id] = True  # launched before verification
        if self.fail_start:
            self.stop(runtime)
            raise LabError(self.fail_start, "start failed")
        rt = dict(runtime, limits={}, startup={"endpoint": "https://127.0.0.1:1"})
        self.started.append(spec)
        return rt

    def observe(self, runtime):
        gid = runtime["generation_id"]
        if self.alive.get(gid):
            return RuntimeObservation(True)
        state, reason = self.terminal.get(gid, ("FAILED", "runtime-lost"))
        return RuntimeObservation(False, state, reason)

    def stop(self, runtime):
        self.alive[runtime["generation_id"]] = False
        self.stopped.append(runtime["generation_id"])
        return {"stopped": True}

    def call(self, runtime, op, params=None, timeout=90.0):
        if not self.alive.get(runtime["generation_id"]):
            raise LabError("transport_error", "gone")
        if op == "baseline":
            return {"identity": {"account": "000000000000"}, "s3_buckets": []}
        if op == "seed":
            if not self.seed_ok:
                return {"ok": False, "mapping": {"bucket": "b"}, "failed": {"logical_id": "subnet"}}
            return {"ok": True, "mapping": {"bucket": "b", "vpc": "vpc-1"}, "steps": []}
        if op == "verify":
            return {"passed": True, "assertions": [{"id": "x", "passed": True}], "observed": {}}
        if op == "aws":
            return {"service": "s3", "operation": params["tokens"][1], "mutating": params["tokens"][1] != "list-buckets", "response": {}}
        if op in ("set-lease", "metrics"):
            return {}
        raise AssertionError(op)

    def inspect(self, runtime):
        return {}

    def cleanup(self, runtime):
        pass

    def endpoint_context(self, runtime):
        return {"url": "https://127.0.0.1:1", "context": "fake", "reachable_from_host": False}


@pytest.fixture
def lab(settings, monkeypatch):
    fake = FakeBackend()

    def get_backend(name, _settings):
        if name != "local":
            raise LabError("unsupported_backend", "no")
        return fake

    monkeypatch.setattr(lifecycle, "get_backend", get_backend)
    monkeypatch.setattr(lifecycle, "foreign_units", lambda s, known: [])
    lab = lifecycle.Lab(settings)
    lab.fake = fake
    return lab


def test_create_ready_empty(lab):
    res = lab.create("one")
    assert res["state"] == "READY" and res["fixture"]["state"] == "EMPTY"
    assert res["generation"]["number"] == 1


def test_single_slot(lab):
    lab.create("one")
    with pytest.raises(LabError) as exc:
        lab.create("two")
    assert exc.value.code == "slot_busy" and exc.value.exit_code == 4
    with pytest.raises(LabError) as exc:
        lab.create("one")
    assert exc.value.code == "name_conflict" and exc.value.exit_code == 3


def test_foreign_unit_blocks_slot(lab, monkeypatch):
    monkeypatch.setattr(lifecycle, "foreign_units", lambda s, known: ["ono-sendai-lab-x.service"])
    with pytest.raises(LabError) as exc:
        lab.create("one")
    assert exc.value.code == "slot_busy"


def test_unsupported_backend_has_no_side_effects(lab, settings):
    with pytest.raises(LabError) as exc:
        lab.create("one", backend="remote-vm")
    assert exc.value.code == "unsupported_backend"
    assert lab.registry.all() == []


def test_start_failure_records_failed_and_frees_slot(lab):
    lab.fake.fail_start = "startup_failed"
    with pytest.raises(LabError):
        lab.create("one")
    rec = lab.registry.get("one")
    assert rec["state"] == "FAILED"
    lab.fake.fail_start = None
    assert lab.create("two")["state"] == "READY"


def test_reset_rotates_generation_and_rejects_stale(lab):
    first = lab.create("one")["generation"]["id"]
    res = lab.reset("one")
    assert res["previous_generation"] == first and res["generation"]["id"] != first
    assert first in lab.fake.stopped
    with pytest.raises(LabError) as exc:
        lab.aws("one", ["s3", "list-buckets"], generation=first)
    assert exc.value.code == "stale_generation" and exc.value.exit_code == 3
    with pytest.raises(LabError):
        lab.credentials("one", generation=first)


def test_seed_semantics(lab):
    lab.create("one")
    assert lab.seed("one", "aws-minimal")["fixture_state"] == "VERIFIED"
    with pytest.raises(LabError) as exc:
        lab.seed("one", "aws-minimal")
    assert exc.value.code == "fixture_state_conflict"
    lab.reset("one")
    lab.fake.seed_ok = False
    with pytest.raises(LabError) as exc:
        lab.seed("one", "aws-minimal")
    assert exc.value.code == "seed_failed"
    assert lab.registry.get("one")["fixture"]["state"] == "DIRTY"
    with pytest.raises(LabError) as exc:
        lab.seed("one", "aws-minimal")
    assert exc.value.code == "fixture_state_conflict"


def test_user_mutation_marks_dirty_reads_do_not(lab):
    lab.create("one")
    lab.seed("one", "aws-minimal")
    lab.aws("one", ["s3", "list-buckets"])
    assert lab.registry.get("one")["fixture"]["state"] == "VERIFIED"
    lab.aws("one", ["s3", "create-bucket", "--bucket", "abc"])
    assert lab.registry.get("one")["fixture"]["state"] == "DIRTY"


def test_dead_runtime_never_reported_ready(lab):
    gen = lab.create("one")["generation"]["id"]
    lab.fake.alive[gen] = False
    lab.fake.terminal[gen] = ("FAILED", "emulator-died")
    res = lab.status("one")
    assert res["state"] == "FAILED" and res["state_reason"] == "emulator-died"
    with pytest.raises(LabError) as exc:
        lab.aws("one", ["s3", "list-buckets"])
    assert exc.value.code == "not_ready"


def test_expiry_and_no_revival(lab):
    gen = lab.create("one")["generation"]["id"]
    lab.fake.alive[gen] = False
    lab.fake.terminal[gen] = ("EXPIRED", "lease-expired")
    assert lab.status("one")["state"] == "EXPIRED"
    with pytest.raises(LabError) as exc:
        lab.extend("one", 5)
    assert exc.value.code == "lease_expired"


def test_lease_past_deadline_is_expired_even_if_alive(lab):
    lab.create("one")
    rec = lab.registry.get("one")
    rec["generation"]["lease_deadline"] = time.time() - 60
    lab.registry.save(rec)
    assert lab.status("one")["state"] == "EXPIRED"


def test_extend_cap(lab):
    lab.create("one")
    assert lab.extend("one", 30)["lease_deadline"]
    with pytest.raises(LabError) as exc:
        lab.extend("one", 61)
    assert exc.value.code == "lease_cap_exceeded" and exc.value.exit_code == 4
    for bad in (0, -1):
        with pytest.raises(LabError) as exc:
            lab.extend("one", bad)
        assert exc.value.code == "invalid_argument"


def test_interrupted_operation_reconciles_to_failed(lab):
    gen = lab.create("one")["generation"]["id"]
    rec = lab.registry.get("one")
    rec["state"] = "STARTING"
    rec["operation"] = {"kind": "start"}
    lab.registry.save(rec)
    res = lab.status("one")
    assert res["state"] == "FAILED" and "interrupted" in res["state_reason"]
    assert gen in lab.fake.stopped


def test_interrupted_seed_is_dirty(lab):
    lab.create("one")
    rec = lab.registry.get("one")
    rec["fixture"]["state"] = "SEEDING"
    rec["operation"] = {"kind": "seed"}
    lab.registry.save(rec)
    res = lab.status("one")
    assert res["state"] == "READY" and res["fixture"]["state"] == "DIRTY"


def test_reboot_reconciles_without_signals(lab, monkeypatch):
    gen = lab.create("one")["generation"]["id"]
    lab.fake.alive[gen] = False
    lab.fake.terminal[gen] = ("FAILED", "host-reboot")
    assert lab.status("one")["state_reason"] == "host-reboot"


def test_destroy_idempotent(lab):
    lab.create("one")
    assert lab.destroy("one")["destroyed"] is True
    assert lab.destroy("one") == {"name": "one", "destroyed": False, "absent": True}
    assert lab.registry.all() == []


def test_status_has_no_credentials(lab):
    lab.create("one")
    text = repr(lab.status("one"))
    assert "secret" not in text.lower() and "aws_access_key_id" not in text


def test_killed_start_is_found_and_stopped_by_prepared_identity(lab):
    """Regression: a CLI killed while the unit was being created left a
    running unit whose name was never recorded; destroy then dropped the
    record and the generation kept the slot."""
    lab.fake.fail_start = None
    real_start = lab.fake.start

    def killed_start(spec, runtime, logs_dir):
        lab.fake.alive[spec.generation_id] = True
        raise SystemExit("SIGKILL simulated: no cleanup code runs")

    lab.fake.start = killed_start
    with pytest.raises(SystemExit):
        lab.create("one")
    lab.fake.start = real_start
    rec = lab.registry.get("one")
    gen = rec["generation"]["id"]
    # what a SIGKILL leaves on disk: STARTING, the operation marker, the prepared runtime
    rec["state"] = "STARTING"
    rec["operation"] = {"kind": "start"}
    lab.registry.save(rec)
    assert rec["generation"]["runtime"]["unit"] == f"u-{gen}"
    assert lab.status("one")["state"] == "FAILED"
    assert gen in lab.fake.stopped and lab.fake.alive[gen] is False


def test_read_only_aws_reports_expiry_consistently(lab):
    """Regression: the shared-lock read path said not_ready for an EXPIRED sandbox."""
    gen = lab.create("one")["generation"]["id"]
    lab.fake.alive[gen] = False
    lab.fake.terminal[gen] = ("EXPIRED", "lease-expired")
    lab.status("one")
    for tokens in (["s3", "list-buckets"], ["s3", "create-bucket", "--bucket", "abc"]):
        with pytest.raises(LabError) as exc:
            lab.aws("one", tokens)
        assert exc.value.code == "lease_expired"
