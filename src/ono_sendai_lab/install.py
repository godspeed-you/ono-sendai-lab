"""Dependency lock and install receipt handling (LAB-011, LAB-012).

Stdlib only: used by bootstrap before any virtual environment exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from pathlib import Path

from .errors import LabError
from .fsutil import read_json_nofollow

HEX64 = re.compile(r"^[0-9a-f]{64}$")
FPR = re.compile(r"^[0-9A-F]{40}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def load_lock(path: Path) -> dict:
    """Load and structurally validate the emulator lock. Never resolves 'latest'."""
    try:
        lock = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LabError("install_invalid", "emulator lock is unreadable", {"lock": str(path)}) from exc
    problems = []
    if lock.get("lock_version") != 1:
        problems.append("lock_version must be 1")
    if not VERSION.match(str(lock.get("version", ""))):
        problems.append("version must be MAJOR.MINOR.PATCH")
    asset = lock.get("asset", {})
    url = str(asset.get("url", ""))
    if not url.startswith("https://downloads.aws.com/aws-emulator/packages/aws-emulator/"):
        problems.append("asset url must be an authoritative https downloads.aws.com URL")
    if "latest" in url.split("/"):
        problems.append("asset url must not use the 'latest' channel")
    if not HEX64.match(str(asset.get("sha256", ""))):
        problems.append("asset sha256 missing or malformed")
    if not isinstance(asset.get("size"), int) or asset["size"] <= 0:
        problems.append("asset size missing")
    sig = lock.get("signature", {})
    if not str(sig.get("url", "")).startswith("https://downloads.aws.com/"):
        problems.append("signature url must be https downloads.aws.com")
    if not FPR.match(str(sig.get("publisher_key_fingerprint", ""))):
        problems.append("publisher key fingerprint malformed")
    for key in ("sha256", "publisher_key_sha256"):
        if not HEX64.match(str(sig.get(key, ""))):
            problems.append(f"signature {key} malformed")
    exe = lock.get("executable", {})
    if not HEX64.match(str(exe.get("sha256", ""))):
        problems.append("executable sha256 malformed")
    if not isinstance(exe.get("version_output"), list) or not exe["version_output"]:
        problems.append("executable version_output missing")
    plat = lock.get("platform", {})
    if plat.get("os") != "linux" or not plat.get("machine"):
        problems.append("platform must name linux and a machine")
    if problems:
        raise LabError("install_invalid", "emulator lock failed validation", {"problems": problems})
    return lock


def check_platform(lock: dict) -> dict:
    machine = platform.machine()
    system = platform.system().lower()
    info = {"system": system, "machine": machine}
    if system != lock["platform"]["os"] or machine != lock["platform"]["machine"]:
        raise LabError(
            "preflight_failed",
            "host platform does not match the locked artifact",
            {"host": info, "locked": lock["platform"]},
        )
    return info


def os_release() -> dict:
    data = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key] = value.strip().strip('"')
    except OSError:
        pass
    return {"id": data.get("ID"), "version_id": data.get("VERSION_ID"), "pretty_name": data.get("PRETTY_NAME")}


def emulator_dir(settings, lock: dict) -> Path:
    return settings.emulator_root / lock["version"]


def emulator_executable(settings, lock: dict) -> Path:
    return emulator_dir(settings, lock) / "bin" / "aws-emulator"


def requirements_sha256(settings) -> str:
    return sha256_file(settings.repo_root / "locks" / "requirements-runtime.txt")


def load_receipt(settings) -> dict | None:
    try:
        return read_json_nofollow(settings.receipt_path)
    except FileNotFoundError:
        return None


def verify_install(settings, *, full_digest: bool = False) -> dict:
    """Verify that the installed emulator and runtime venv match the lock.

    The fast path compares the receipt with the lock and the executable's
    (size, mtime, inode); ``full_digest`` re-hashes the executable.
    """
    lock = load_lock(settings.lock_path)
    receipt = load_receipt(settings)
    if receipt is None:
        raise LabError("install_invalid", "lab is not installed; run scripts/bootstrap.sh", {"remedy": "scripts/bootstrap.sh"})
    exe = emulator_executable(settings, lock)
    problems = []
    if receipt.get("version") != lock["version"]:
        problems.append("installed version differs from lock")
    if receipt.get("executable_sha256") != lock["executable"]["sha256"]:
        problems.append("receipt executable digest differs from lock")
    if receipt.get("asset_sha256") != lock["asset"]["sha256"]:
        problems.append("receipt asset digest differs from lock")
    try:
        st = os.stat(exe)
        if (st.st_size, st.st_mtime_ns, st.st_ino) != (
            receipt.get("executable_size"),
            receipt.get("executable_mtime_ns"),
            receipt.get("executable_inode"),
        ):
            problems.append("executable changed since verification")
        if st.st_mode & 0o022:
            problems.append("executable is group/world writable")
    except FileNotFoundError:
        problems.append("emulator executable missing")
    venv_python = settings.runtime_venv / "bin" / "python"
    if not venv_python.exists():
        problems.append("runtime venv missing")
    elif receipt.get("runtime_requirements_sha256") != requirements_sha256(settings):
        problems.append("runtime venv was built from a different requirements lock")
    digest = None
    if full_digest and not problems:
        digest = sha256_file(exe)
        if digest != lock["executable"]["sha256"]:
            problems.append("executable digest mismatch")
    if problems:
        raise LabError("install_invalid", "installed lab does not match the lock", {"problems": problems, "remedy": "scripts/bootstrap.sh"})
    return {
        "version": lock["version"],
        "executable_sha256": lock["executable"]["sha256"],
        "asset_sha256": lock["asset"]["sha256"],
        "full_digest_checked": bool(full_digest),
        "signature_verified_at_install": receipt.get("signature_verified"),
        "version_output": receipt.get("version_output"),
    }
