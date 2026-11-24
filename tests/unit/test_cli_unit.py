import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI = os.path.join(ROOT, "bin", "aws-lab")


def run(args, tmp_path, extra_env=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
    }
    env.update(extra_env or {})
    proc = subprocess.run([CLI, *args], capture_output=True, text=True, env=env, timeout=60)
    return proc


def test_version_and_help_without_emulator(tmp_path):
    proc = run(["--version"], tmp_path)
    assert proc.returncode == 0 and proc.stdout.strip() == "aws-lab 0.1.0"
    proc = run(["--help"], tmp_path)
    assert proc.returncode == 0 and "doctor" in proc.stdout
    proc = run(["create", "--help"], tmp_path)
    assert proc.returncode == 0 and "--name" in proc.stdout


@pytest.mark.parametrize(
    "args,code,err",
    [
        (["--json", "nope"], 2, "usage_error"),
        (["--json"], 2, "usage_error"),
        (["--json", "create"], 2, "usage_error"),
        (["--json", "create", "--name", "../etc"], 2, "invalid_name"),
        (["--json", "status", "a/b"], 2, "invalid_name"),
        (["--json", "status", "missing"], 3, "not_found"),
        (["--json", "create", "--name", "x", "--backend", "remote-vm"], 4, "unsupported_backend"),
        (["--json", "create", "--name", "x", "--backend", "gcp"], 4, "unsupported_backend"),
        (["--json", "aws", "x"], 2, "usage_error"),
        (["--json", "aws", "x", "--", "iam", "list-users"], 2, "operation_not_allowed"),
        (["--json", "aws", "x", "--", "s3", "list-buckets", "--endpoint-url", "https://x"], 2, "operation_not_allowed"),
        (["--json", "status", "--", "x"], 2, "usage_error"),
        (["--json", "logs", "x", "--tail", "201"], 2, "invalid_argument"),
        (["--json", "seed", "x", "--scenario", "aws-degraded"], 2, "unknown_scenario"),
        (["--json", "extend", "x", "--minutes", "abc"], 2, "usage_error"),
    ],
)
def test_errors_are_single_json_documents(tmp_path, args, code, err):
    proc = run(args, tmp_path)
    assert proc.returncode == code, proc.stderr
    doc = json.loads(proc.stdout)
    assert proc.stdout.count("\n") == 1
    assert doc["ok"] is False and doc["error"]["code"] == err and doc["error"]["exit_code"] == code
    assert doc["schema_version"] == 1


def test_human_errors_go_to_stderr(tmp_path):
    proc = run(["status", "missing"], tmp_path)
    assert proc.returncode == 3 and proc.stdout == "" and "not_found" in proc.stderr


def test_status_empty_and_scenarios(tmp_path):
    doc = json.loads(run(["--json", "status"], tmp_path).stdout)
    assert doc["ok"] and doc["result"] == {"sandboxes": [], "active_generations": 0}
    doc = json.loads(run(["--json", "scenarios"], tmp_path).stdout)
    assert [s["id"] for s in doc["result"]["scenarios"]] == ["aws-empty", "aws-minimal"]


def test_ambient_python_variables_cannot_inject_code(tmp_path):
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "json.py").write_text("raise SystemExit('injected')\n")
    proc = run(["--json", "--version"], tmp_path, {"PYTHONPATH": str(evil), "PYTHONSTARTUP": str(evil / "json.py")})
    assert proc.returncode == 0 and json.loads(proc.stdout)["ok"]


def test_destroy_absent_succeeds_without_state(tmp_path):
    proc = run(["--json", "destroy", "ghost"], tmp_path)
    doc = json.loads(proc.stdout)
    assert proc.returncode == 0 and doc["result"]["absent"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
