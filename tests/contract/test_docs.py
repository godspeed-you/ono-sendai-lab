"""A18 documentation review checks (LAB-080, LAB-081, LAB-083)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "docs/STATE.md",
    "docs/specs/ono_sendai_lab_spec_v0.1.0.md",
    "docs/adr",
    "docs/contracts",
    "docs/operations",
    "scenarios",
    "scripts/bootstrap.sh",
    "scripts/gate.sh",
    "scripts/acceptance.sh",
    "scripts/release-check.sh",
    "tests",
    ".github/workflows",
]
OPERATIONS_TOPICS = {
    "installation": ["bootstrap.sh --plan", "bootstrap.sh --dev", "no sudo"],
    "preflight remedies": ["apparmor", "bwrap-userns-restrict", "systemctl --user", "linger"],
    "uninstall": ["--uninstall"],
    "lifecycle examples": ["aws-lab create --name", "aws-lab seed", "aws-lab reset", "aws-lab destroy", "aws-lab extend"],
    "troubleshooting": ["slot_busy", "startup_failed", "lease_expired"],
    "resource accounting": ["5,000,000,000", "1,073,741,824", "memory.peak", "hard quota"],
    "pinned upgrade": ["versions.json", "emulator.lock.json", "gpg"],
    "security model": ["same-UID", "threat", "kernel"],
    "tested host": ["Ubuntu 26.04", "Ubuntu 24.04"],
    "vm boundary": ["WireGuard", "remote-vm", "live memory migration"],
}
ADR_TOPICS = ["dependency", "containment", "process ownership", "client", "generation", "backend"]


def _doc_text() -> str:
    parts = []
    for path in sorted((ROOT / "docs" / "operations").glob("*.md")) + sorted((ROOT / "docs" / "adr").glob("*.md")):
        parts.append(path.read_text())
    return "\n".join(parts)


def test_A18_documentation_is_complete_and_consistent():
    for rel in REQUIRED:
        assert (ROOT / rel).exists(), f"required path missing: {rel}"
    readme = (ROOT / "README.md").read_text()
    for heading in ("Purpose", "Quickstart", "Prerequisites", "Limitations"):
        assert re.search(rf"^##+ .*{heading}", readme, re.M), f"README lacks {heading}"
    text = _doc_text()
    for topic, needles in OPERATIONS_TOPICS.items():
        for needle in needles:
            assert needle.lower() in text.lower(), f"operations docs lack {topic}: {needle!r}"
    adrs = "\n".join(p.read_text().lower() for p in (ROOT / "docs" / "adr").glob("*.md"))
    for topic in ADR_TOPICS:
        assert topic in adrs, f"no ADR covers {topic}"
    # relative links resolve
    for md in [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CONTRIBUTING.md", *ROOT.glob("docs/**/*.md")]:
        if "specs" in md.parts:
            continue
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (md.parent / target).exists(), f"{md.relative_to(ROOT)} links to missing {target}"
    # version and contract metadata agree
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    assert f'__version__ = "{version}"' in (ROOT / "src" / "ono_sendai_lab" / "__init__.py").read_text()
    state = (ROOT / "docs" / "STATE.md").read_text()
    assert version in state and "ea257cb2da7da02a7ed6d9c3ac9f192ee3e14ad18a6b31cd512bb18c09862d0d" in state
    # Licence (LAB-080): chosen by the operator, recorded consistently.
    license_text = (ROOT / "LICENSE").read_text()
    assert license_text.lstrip().startswith("Apache License") and "Version 2.0, January 2004" in license_text
    assert 'license = "Apache-2.0"' in (ROOT / "pyproject.toml").read_text()
    assert "Apache License 2.0" in readme and "Apache-2.0" in state


def test_A18_review_record_exists():
    review = ROOT / "docs" / "evidence" / "doc-review.md"
    assert review.exists(), "documentation review record missing"
    text = review.read_text()
    for item in ("fresh install", "usage", "uninstall", "host prerequisites", "security limitations", "VM migration"):
        assert item.lower() in text.lower(), f"review record does not cover {item}"
    assert re.search(r"Reviewer:", text) and re.search(r"Date: 20\d\d-\d\d-\d\d", text)
