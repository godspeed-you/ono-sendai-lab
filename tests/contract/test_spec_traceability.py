"""Spec adoption and requirement-to-test traceability (LAB-081, LAB-100)."""

import hashlib
from pathlib import Path

from ono_sendai_lab import devtools

ROOT = Path(__file__).resolve().parents[2]


def test_adopted_spec_is_byte_identical():
    spec = ROOT / "docs" / "specs" / "ono_sendai_lab_spec_v0.1.0.md"
    assert hashlib.sha256(spec.read_bytes()).hexdigest() == devtools.SPEC_SHA256
    assert devtools.check_spec() == []


def test_every_requirement_and_acceptance_row_is_traced():
    assert len(devtools.spec_requirement_ids()) == 42
    assert devtools.check_traceability() == []


def test_repository_secret_scan_is_clean():
    assert devtools.secret_scan() == []


def test_secret_scan_patterns_detect_planted_material():
    text = "EMULATOR" + "ABCDEF012345" + "\n" + "AKIA" + "Q" * 16 + "\n" + "path /home/" + "someone/x\n"
    kinds = {name for name, pat in devtools.SECRET_PATTERNS.items() if pat.search(text)}
    assert {"emulator-access-key", "aws-access-key", "private-home-path"} <= kinds
