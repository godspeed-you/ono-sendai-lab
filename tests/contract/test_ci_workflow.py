"""A19 CI configuration review (LAB-092)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "ci.yml"


def test_A19_workflow_uses_the_gates_with_least_privilege():
    text = WF.read_text()
    assert re.search(r"^permissions:\n  contents: read$", text, re.M)
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses
    assert "scripts/gate.sh" in text and "scripts/acceptance.sh" in text
    assert re.search(r"^  gate:", text, re.M) and re.search(r"^  acceptance:", text, re.M)
    assert "needs: gate" in text
    assert "continue-on-error" not in text
    assert "secrets." not in text and "AWS_ACCESS_KEY" not in text
    assert "self-hosted" not in text
    # Exactly one privileged command, approved by the operator on 2026-11-24:
    # installing the distribution's bubblewrap on the ephemeral hosted runner.
    sudo_lines = [line.strip() for line in re.sub(r"#.*", "", text).splitlines() if "sudo" in line]
    assert sudo_lines == ["run: sudo apt-get install -y --no-install-recommends bubblewrap"], sudo_lines
    code = re.sub(r"#.*", "", text)
    assert "sysctl" not in code and "apparmor_parser" not in code
    assert "persist-credentials: false" in text


def test_A19_ci_status_reported_separately():
    state = (ROOT / "docs" / "STATE.md").read_text()
    section = state.split("## CI status", 1)
    assert len(section) == 2, "docs/STATE.md needs a '## CI status' section"
    body = section[1].split("\n## ", 1)[0]
    assert re.search(r"https://github\.com/godspeed-you/ono-sendai-lab/actions/runs/\d+", body) or "blocker" in body.lower()
    assert "local" in body.lower()
