"""A16 contract summary, A17 measurements and coverage, A18/A19 documentation and CI review."""

import json
import os
import sys
import time
from pathlib import Path

import pytest
from labharness import ROOT, RUN_DIR

sys.path.insert(0, str(ROOT / "tests" / "contract"))


@pytest.mark.row("A16")
def test_A16_all_json_documents_followed_the_contract(lab):
    docs = [json.loads(line) for line in (RUN_DIR / "cli-docs.jsonl").read_text().splitlines()]
    assert len(docs) > 100, "the acceptance run must have exercised the CLI broadly"
    commands = {d["doc"]["command"] for d in docs}
    assert {"doctor", "create", "status", "credentials", "seed", "reset", "destroy", "extend", "logs", "aws"} <= commands
    exit_codes = {d["rc"] for d in docs}
    assert {0, 2, 3, 4, 5} <= exit_codes
    for d in docs:
        if not d["doc"]["ok"]:
            assert d["rc"] == d["doc"]["error"]["exit_code"]
    from ono_sendai_lab.devtools import secret_scan

    assert secret_scan([RUN_DIR / "cli-docs.jsonl"]) == []
    # Human output agrees with JSON and keeps stdout clean on errors.
    lab.ok("create", "--name", "a16")
    human = lab.cli("status", "a16", as_json=False)
    assert human.rc == 0 and "a16: READY fixture=EMPTY" in human.stdout
    bad = lab.cli("seed", "a16", "--scenario", "aws-nope", as_json=False)
    assert bad.rc == 2 and bad.stdout == "" and "unknown_scenario" in bad.stderr
    for name in ("../a16", "a16/..", "a" * 49):
        lab.fail("invalid_name", 2, "status", name)
    lab.fail("not_found", 3, "logs", "a16-missing")
    logs = "\n".join(lab.ok("logs", "a16", "--tail", "200")["lines"])
    assert "EMULATOR" not in logs.replace("EMULATOR_TELEMETRY", "")
    lab.ok("destroy", "a16")


@pytest.mark.row("A17")
def test_A17_measurements_and_operation_coverage(lab):
    t0 = time.monotonic()
    lab.ok("create", "--name", "a17")
    create_s = round(time.monotonic() - t0, 2)
    rt = lab.runtime("a17")
    cg = Path(f"/sys/fs/cgroup{rt['control_group']}")

    def sample():
        usage = {
            "memory_current_bytes": int((cg / "memory.current").read_text()),
            "memory_peak_bytes": int((cg / "memory.peak").read_text()),
            "swap_current_bytes": int((cg / "memory.swap.current").read_text()),
        }
        for line in (cg / "cpu.stat").read_text().splitlines():
            key, value = line.split()
            if key in ("usage_usec", "nr_throttled"):
                usage[f"cpu_{key}"] = int(value)
        procs = lab.ok("status", "a17")["resources"]["processes"]
        usage["emulator_rss_bytes"] = procs["emulator"].get("VmRSS")
        usage["emulator_hwm_bytes"] = procs["emulator"].get("VmHWM")
        usage["supervisor_rss_bytes"] = procs["supervisor"].get("VmRSS")
        return usage

    time.sleep(5)
    idle = sample()
    t1 = time.monotonic()
    lab.ok("seed", "a17", "--scenario", "aws-minimal")
    for _ in range(10):
        lab.ok("aws", "a17", "--", "ec2", "describe-vpcs")
    normal_s = round(time.monotonic() - t1, 2)
    normal = sample()
    assert normal["memory_peak_bytes"] < 1_073_741_824 and normal["swap_current_bytes"] == 0
    t2 = time.monotonic()
    lab.ok("reset", "a17", "--scenario", "aws-minimal")
    reset_s = round(time.monotonic() - t2, 2)
    t3 = time.monotonic()
    lab.ok("destroy", "a17")
    destroy_s = round(time.monotonic() - t3, 2)
    disk = lab.ok("doctor")["checks"]["disk"]
    from labharness import CLI, run_measured

    cli_rss = {}
    for label, args in (("status", ["--json", "status"]), ("scenarios", ["--json", "scenarios"])):
        rc, _, _, rss = run_measured([str(CLI), *args], lab.env)
        assert rc == 0
        cli_rss[label] = rss
    measurements = {
        "idle_after_5s": idle,
        "after_aws_minimal_seed_and_10_reads": normal,
        "durations_seconds": {"create": create_s, "seed_plus_10_reads": normal_s, "reset_with_scenario": reset_s, "destroy": destroy_s},
        "host_cli_peak_rss_kib": cli_rss,
        "disk": disk,
        "load_average": os.getloadavg(),
    }
    (RUN_DIR / "measurements.json").write_text(json.dumps(measurements, indent=2, sort_keys=True))
    # Operation coverage: every allowlisted operation was observed succeeding against the pinned binary.
    from ono_sendai_lab.operations import OPERATIONS

    seen = {json.loads(line)["operation"] for line in (RUN_DIR / "operations.jsonl").read_text().splitlines()}
    expected = {f"{svc} {op}" for (svc, op) in OPERATIONS}
    assert expected <= seen, sorted(expected - seen)


@pytest.mark.row("A18")
def test_A18_documentation_review():
    import test_docs

    test_docs.test_A18_documentation_is_complete_and_consistent()
    test_docs.test_A18_review_record_exists()


@pytest.mark.row("A19")
def test_A19_ci_configuration():
    import test_ci_workflow

    test_ci_workflow.test_A19_workflow_uses_the_gates_with_least_privilege()
    test_ci_workflow.test_A19_ci_status_reported_separately()
