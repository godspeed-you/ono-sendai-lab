"""Gate helpers: secret scan, code hash, traceability, compatibility and evidence checks.

Used by scripts/gate.sh, scripts/acceptance.sh and scripts/release-check.sh.
Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "specs" / "ono_sendai_lab_spec_v0.1.0.md"
SPEC_SHA256 = "ea257cb2da7da02a7ed6d9c3ac9f192ee3e14ad18a6b31cd512bb18c09862d0d"
ACCEPTANCE_IDS = [f"A{i:02d}" for i in range(1, 20)]
CODE_PATHS = ("src", "scenarios", "locks", "bin", "scripts", "tests", "pyproject.toml")

SECRET_PATTERNS = {
    "emulator-access-key": re.compile(r"\bEMULATOR[0-9A-F]{12}\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "uppercase-hex-40": re.compile(r"(?<![A-Za-z0-9/+])[0-9A-F]{40}(?![A-Za-z0-9/+=])"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "secret-assignment": re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*[A-Za-z0-9/+]{20,}"),
    "private-home-path": re.compile(r"/home/(?!lab\b)[a-z_][a-z0-9_-]*/"),
}
# Synthetic sentinels and public values that are allowed verbatim.
ALLOWLIST = {
    "EMULATOR000000000000",  # wrong-credentials sentinel
    "EMULATOR0123456789AB",  # redaction test fixture
    "0123456789ABCDEF0123456789ABCDEF01234567",  # redaction test fixture
    "AKIAIOSFODNN7EXAMPLE",  # AWS documentation example key
    "98BCF336A3BE53B734E967C52E5BF49BD940F624",  # public OpenPGP fingerprint of the AWS publisher key
    "AKIASENTINEL0000000A",  # ambient-poisoning sentinel
}


def git_files(include_untracked: bool = True) -> list[Path]:
    args = ["git", "-C", str(ROOT), "ls-files", "-z", "--cached"]
    if include_untracked:
        args += ["--others", "--exclude-standard"]
    out = subprocess.run(args, capture_output=True, check=True).stdout
    return sorted({ROOT / p for p in out.decode().split("\0") if p})


def secret_scan(paths: list[Path] | None = None) -> list[dict]:
    findings = []
    for path in paths or git_files():
        if not path.is_file() or path.suffix in (".asc",):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in SECRET_PATTERNS.items():
                for match in pattern.finditer(line):
                    if match.group(0) in ALLOWLIST:
                        continue
                    findings.append({"file": str(path.relative_to(ROOT)), "line": lineno, "kind": name})
    return findings


def code_hash() -> str:
    """Content hash of everything that determines behaviour or tests."""
    digest = hashlib.sha256()
    for path in git_files(include_untracked=True):
        rel = path.relative_to(ROOT).as_posix()
        if not rel.startswith(CODE_PATHS) or not path.is_file():
            continue
        digest.update(rel.encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def spec_requirement_ids() -> list[str]:
    return sorted(set(re.findall(r"\*\*(LAB-[0-9]{3}):\*\*", SPEC.read_text())))


def _test_exists(ref: str) -> bool:
    if "::" not in ref:
        return (ROOT / ref).exists()
    file, name = ref.split("::", 1)
    path = ROOT / file
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text())
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    return name.split("[")[0] in names


def check_traceability() -> list[str]:
    problems = []
    doc = json.loads((ROOT / "docs" / "contracts" / "traceability.json").read_text())
    if doc.get("spec_sha256") != SPEC_SHA256:
        problems.append("traceability spec_sha256 differs from the adopted spec")
    reqs = doc.get("requirements", {})
    for rid in spec_requirement_ids():
        entry = reqs.get(rid)
        if not entry:
            problems.append(f"{rid}: no traceability entry")
            continue
        if not entry.get("tests") and not entry.get("verification"):
            problems.append(f"{rid}: neither tests nor a verification record")
        for ref in entry.get("tests", []):
            if not _test_exists(ref):
                problems.append(f"{rid}: missing test {ref}")
    extra = set(reqs) - set(spec_requirement_ids())
    if extra:
        problems.append(f"unknown requirement ids: {sorted(extra)}")
    rows = doc.get("acceptance", {})
    for aid in ACCEPTANCE_IDS:
        entry = rows.get(aid)
        if not entry or not entry.get("tests"):
            problems.append(f"{aid}: no executable test")
            continue
        for ref in entry["tests"]:
            if not _test_exists(ref):
                problems.append(f"{aid}: missing test {ref}")
    return problems


def check_spec() -> list[str]:
    actual = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    sums = (ROOT / "docs" / "specs" / "SHA256SUMS").read_text().split()
    problems = []
    if actual != SPEC_SHA256:
        problems.append("adopted spec was modified")
    if sums[:2] != [SPEC_SHA256, SPEC.name]:
        problems.append("docs/specs/SHA256SUMS does not record the adopted spec")
    return problems


def check_evidence(path: Path) -> list[str]:
    problems = []
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return [f"evidence {path} missing or unreadable"]
    if doc.get("code_hash") != code_hash():
        problems.append("acceptance evidence was produced for different code")
    if doc.get("spec_sha256") != SPEC_SHA256:
        problems.append("acceptance evidence names a different spec")
    rows = doc.get("acceptance_rows", {})
    for aid in ACCEPTANCE_IDS:
        if rows.get(aid, {}).get("status") != "passed":
            problems.append(f"{aid}: not passed in evidence ({rows.get(aid, {}).get('status')})")
    if doc.get("cleanup", {}).get("verified") is not True:
        problems.append("evidence does not show verified cleanup")
    if secret_scan([path]):
        problems.append("evidence contains secret-like material")
    return problems


def _lock() -> dict:
    return json.loads((ROOT / "locks" / "emulator.lock.json").read_text())


def _botocore_pin() -> str:
    for line in (ROOT / "locks" / "requirements-runtime.txt").read_text().splitlines():
        if line.startswith("botocore=="):
            return line.split("==", 1)[1].split()[0]
    raise SystemExit("botocore pin missing")


OP_NOTES = {
    "s3 create-bucket": (
        "Outside us-east-1 the emulator requires CreateBucketConfiguration.LocationConstraint; the lab sets it "
        "explicitly to the session region (IllegalLocationConstraintException without it)."
    ),
    "sts get-caller-identity": (
        "Wrong keys yield InvalidClientTokenId (HTTP 403) on 1.0.598; the user guide documents AccessDenied for that case."
    ),
    "ec2 describe-vpcs": "A default VPC 172.31.0.0/16 exists in a fresh generation; it is recorded as baseline, never as fixture-owned.",
    "ec2 describe-subnets": "Four default subnets exist in a fresh generation (baseline).",
    "ec2 describe-security-groups": "A default security group exists in a fresh generation (baseline).",
}


def build_compat(run_dir: Path) -> dict:
    from .operations import OPERATIONS

    seen: dict[str, dict] = {}
    for line in (run_dir / "operations.jsonl").read_text().splitlines():
        obs = json.loads(line)
        entry = seen.setdefault(obs["operation"], {"tests": set(), "scenarios": set(), "outcomes": set()})
        entry["tests"].add(obs["test"].split("[")[0])
        entry["outcomes"].add(obs["outcome"])
        if obs.get("scenario"):
            entry["scenarios"].add(obs["scenario"])
    ops = []
    for (svc, name), op in sorted(OPERATIONS.items()):
        key = f"{svc} {name}"
        entry = seen.get(key)
        verified = bool(entry) and entry["outcomes"] == {"ok"}
        ops.append(
            {
                "service": svc,
                "operation": name,
                "surface": "public" if op.public else "fixture-internal",
                "scenarios": sorted(entry["scenarios"]) if entry else [],
                "observed_outcome": "succeeded against the pinned emulator in a contained generation" if verified else "not observed",
                "evidence": "docs/evidence/acceptance-evidence.json",
                "test_reference": sorted(f"tests/acceptance::{t}" for t in entry["tests"]) if entry else ["none"],
                "known_limitation": OP_NOTES.get(key),
                "qualification_status": "verified" if verified else "unqualified",
                "upstream_fidelity_reference": "https://github.com/aws/aws-emulator/releases/tag/v"
                + _lock()["version"]
                + " (scores.json.gz; service-wide, not used for qualification)",
            }
        )
    return {
        "schema_version": 1,
        "emulator_version": _lock()["version"],
        "client": {"name": "botocore", "version": _botocore_pin()},
        "region": "us-west-2",
        "generated_from": "scripts/acceptance.sh (tests/acceptance, real emulator)",
        "operations": ops,
    }


def build_evidence(run_dir: Path) -> dict:
    import platform

    from .install import os_release

    def load(name, default=None):
        path = run_dir / name
        return json.loads(path.read_text()) if path.exists() else default

    rows = load("rows.json", {})
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
    doctor = load("doctor.json", {})
    return {
        "schema_version": 1,
        "kind": "local-acceptance",
        "not_ci_evidence": True,
        "git_head": head,
        "worktree_dirty_at_run": dirty,
        "code_hash": code_hash(),
        "spec_sha256": SPEC_SHA256,
        "emulator": {
            "version": _lock()["version"],
            "asset_sha256": _lock()["asset"]["sha256"],
            "executable_sha256": _lock()["executable"]["sha256"],
        },
        "client": {"botocore": _botocore_pin()},
        "host": {
            "os_release": os_release(),
            "kernel": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "limits": doctor.get("checks", {}).get("resource_controls", {}).get("limits"),
        "isolation": doctor.get("checks", {}).get("network_file_isolation"),
        "acceptance_rows": {
            k: {"status": v["status"], "tests": v["tests"], "durations": v.get("durations", {})} for k, v in sorted(rows.items())
        },
        "measurements": load("measurements.json"),
        "cleanup": load("cleanup.json", {"verified": False}),
        "durations": load("durations.json", {}),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="devtools")
    p.add_argument(
        "command", choices=["secret-scan", "code-hash", "traceability", "spec", "evidence", "admit", "build-compat", "build-evidence"]
    )
    p.add_argument("path", nargs="?")
    p.add_argument("out", nargs="?")
    args = p.parse_args(argv)
    if args.command == "code-hash":
        print(code_hash())
        return 0
    if args.command == "admit":
        from .config import load_settings
        from .errors import LabError
        from .ledger import admit

        try:
            snap = admit(load_settings(), int(args.path), "acceptance run")
        except LabError as exc:
            print(f"admit: refused: {exc.message} {exc.details}", file=sys.stderr)
            return 4
        print(f"admit: ok (used {snap['used_bytes']} of {snap['budget_bytes']} bytes, projected {snap['projected_bytes']})")
        return 0
    if args.command == "build-compat":
        doc = build_compat(Path(args.path))
        (ROOT / "docs" / "contracts" / "operation-compatibility.json").write_text(json.dumps(doc, indent=2) + "\n")
        unverified = [f"{o['service']} {o['operation']}" for o in doc["operations"] if o["qualification_status"] != "verified"]
        print(f"build-compat: {len(doc['operations']) - len(unverified)} verified, unverified: {unverified}")
        return 1 if unverified else 0
    if args.command == "build-evidence":
        doc = build_evidence(Path(args.path))
        Path(args.out).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        print(f"build-evidence: wrote {args.out}")
        return 0
    if args.command == "secret-scan":
        findings = secret_scan()
        for f in findings:
            print(f"secret-scan: {f['file']}:{f['line']}: {f['kind']}", file=sys.stderr)
        print(f"secret-scan: {len(findings)} finding(s)")
        return 1 if findings else 0
    if args.command == "traceability":
        problems = check_traceability()
    elif args.command == "spec":
        problems = check_spec()
    else:
        problems = check_evidence(Path(args.path))
    for problem in problems:
        print(f"{args.command}: {problem}", file=sys.stderr)
    print(f"{args.command}: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
