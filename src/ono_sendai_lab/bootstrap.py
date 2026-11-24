"""Install, verify and uninstall the lab (LAB-011, LAB-012, LAB-013).

Stdlib only. Downloads happen here and nowhere else. No privilege escalation,
no package manager, no upstream maintainer scripts: the signed .deb is
verified and its payload extracted into a private prefix.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import __version__, isolation, ledger
from .config import load_settings
from .errors import LabError
from .fsutil import atomic_write_json, ensure_private_dir, remove_tree
from .install import check_platform, emulator_dir, load_lock, os_release, requirements_sha256, sha256_file
from .redact import redact_obj

VENV_RUNTIME_ESTIMATE = 150_000_000
VENV_DEV_ESTIMATE = 250_000_000
REQUIRED_TOOLS = ("bwrap", "systemd-run", "systemctl", "gpg", "dpkg-deb")
ALLOWED_HOST = "downloads.aws.com"


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(f"https://{ALLOWED_HOST}/"):
            raise LabError("install_invalid", "download redirected away from the authoritative host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str, dest: Path, max_bytes: int, timeout: float = 120.0) -> None:
    if not url.startswith(f"https://{ALLOWED_HOST}/"):
        raise LabError("install_invalid", "refusing non-authoritative download URL")
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _HttpsOnlyRedirect())
    tmp = dest.with_name(dest.name + ".part")
    written = 0
    with opener.open(url, timeout=timeout) as resp, open(tmp, "wb") as out:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            written += len(block)
            if written > max_bytes:
                out.close()
                tmp.unlink()
                raise LabError("install_invalid", "download exceeds the locked size")
            out.write(block)
    os.replace(tmp, dest)


def preflight(settings, lock: dict) -> dict:
    report: dict = {"lab_version": __version__, "os_release": os_release()}
    report["platform"] = check_platform(lock)
    if sys.version_info < (3, 12):  # noqa: UP036 - bootstrap may run on an older system python
        raise LabError("preflight_failed", "Python 3.12 or newer is required", {"python": sys.version.split()[0]})
    report["python"] = sys.version.split()[0]
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    if "bwrap" in settings.simulate_missing:
        missing.append("bwrap")
    if missing:
        raise LabError(
            "preflight_failed",
            "required host tools are missing",
            {"missing": sorted(set(missing)), "remedy": "see docs/operations/installation.md#prerequisites"},
        )
    if settings.runtime_dir is None:
        raise LabError("preflight_failed", "XDG_RUNTIME_DIR is not set; a systemd user session is required")
    ns = isolation.probe_namespace(settings)
    if not ns.get("ok"):
        raise LabError("isolation_unavailable", "network/file containment cannot be established", {"probe": ns})
    rc = isolation.probe_resource_controls(settings)
    if not rc.get("ok"):
        raise LabError("resource_control_unavailable", "cgroup resource limits cannot be enforced", {"probe": rc})
    report["containment"] = {"ok": True, "interfaces": ns.get("interfaces"), "external_probe": ns.get("external_probe")}
    report["resource_controls"] = rc
    return report


def plan(settings, lock: dict, dev: bool) -> list[dict]:
    steps = []
    deb = settings.downloads_dir / lock["asset"]["filename"]
    steps.append(
        {
            "step": "download-asset",
            "needed": not deb.exists(),
            "url": lock["asset"]["url"],
            "bytes": lock["asset"]["size"],
        }
    )
    steps.append({"step": "verify-asset-sha256", "expected": lock["asset"]["sha256"]})
    steps.append({"step": "verify-openpgp-signature", "fingerprint": lock["signature"]["publisher_key_fingerprint"]})
    exe = emulator_dir(settings, lock) / "bin" / "aws-emulator"
    steps.append({"step": "extract-executable", "needed": not exe.exists(), "bytes": lock["executable"]["size"]})
    steps.append({"step": "verify-executable", "expected": lock["executable"]["sha256"]})
    steps.append({"step": "runtime-venv", "needed": not _venv_current(settings.runtime_venv, settings, "runtime")})
    if dev:
        steps.append({"step": "dev-venv", "needed": not _venv_current(settings.dev_venv, settings, "dev")})
    steps.append({"step": "write-receipt", "path_kind": "data/install-receipt.json"})
    return steps


def _projected_bytes(settings, lock: dict, dev: bool) -> int:
    total = 0
    deb = settings.downloads_dir / lock["asset"]["filename"]
    if not deb.exists():
        total += lock["asset"]["size"]
    if not (emulator_dir(settings, lock) / "bin" / "aws-emulator").exists():
        total += lock["executable"]["size"]
    if not _venv_current(settings.runtime_venv, settings, "runtime"):
        total += VENV_RUNTIME_ESTIMATE
    if dev and not _venv_current(settings.dev_venv, settings, "dev"):
        total += VENV_DEV_ESTIMATE
    return total


def _req_file(settings, kind: str) -> Path:
    return settings.repo_root / "locks" / f"requirements-{kind}.txt"


def _venv_current(venv: Path, settings, kind: str) -> bool:
    marker = venv / ".lab-requirements.sha256"
    try:
        return marker.read_text().strip() == sha256_file(_req_file(settings, kind)) and (venv / "bin" / "python").exists()
    except OSError:
        return False


def _ensure_asset(settings, lock: dict, offline: bool) -> Path:
    ensure_private_dir(settings.cache_dir)
    ensure_private_dir(settings.downloads_dir)
    deb = settings.downloads_dir / lock["asset"]["filename"]
    sig = settings.downloads_dir / (lock["asset"]["filename"] + ".sig")
    if not deb.exists():
        if offline:
            raise LabError("install_invalid", "asset not cached and --offline given")
        _download(lock["asset"]["url"], deb, lock["asset"]["size"])
    if not sig.exists():
        if offline:
            raise LabError("install_invalid", "signature not cached and --offline given")
        _download(lock["signature"]["url"], sig, 65536)
    size = deb.stat().st_size
    digest = sha256_file(deb)
    if size != lock["asset"]["size"] or digest != lock["asset"]["sha256"]:
        raise LabError(
            "install_invalid",
            "downloaded asset does not match the locked digest; refusing to install",
            {
                "expected_sha256": lock["asset"]["sha256"],
                "actual_sha256": digest,
                "size": size,
                "remedy": "delete the cached download and retry; investigate if it repeats",
            },
        )
    if sha256_file(sig) != lock["signature"]["sha256"]:
        raise LabError("install_invalid", "signature file does not match the locked digest")
    return deb


def _verify_signature(settings, lock: dict, deb: Path) -> dict:
    key = settings.repo_root / lock["signature"]["publisher_key_file"]
    if sha256_file(key) != lock["signature"]["publisher_key_sha256"]:
        raise LabError("install_invalid", "pinned publisher key file does not match the lock")
    ensure_private_dir(settings.tmp_dir)
    home = Path(tempfile.mkdtemp(prefix="gnupg-", dir=settings.tmp_dir))
    try:
        os.chmod(home, 0o700)
        env = {"PATH": "/usr/bin:/bin", "GNUPGHOME": str(home), "LANG": "C"}
        imp = subprocess.run(
            ["gpg", "--batch", "--quiet", "--import", str(key)], capture_output=True, text=True, errors="replace", env=env, timeout=60
        )
        if imp.returncode != 0:
            raise LabError("install_invalid", "could not import the pinned publisher key")
        ver = subprocess.run(
            ["gpg", "--batch", "--status-fd", "1", "--verify", str(deb) + ".sig", str(deb)],
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            timeout=300,
        )
        fpr = lock["signature"]["publisher_key_fingerprint"]
        valid = [line for line in ver.stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
        if ver.returncode != 0 or not valid or valid[0].split()[2] != fpr:
            raise LabError("install_invalid", "OpenPGP signature verification failed", {"expected_fingerprint": fpr})
        grouped = " ".join(fpr[i : i + 4] for i in range(0, len(fpr), 4))
        return {"verified": True, "fingerprint": grouped, "signature_created": valid[0].split()[3]}
    finally:
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "all"], capture_output=True, timeout=30)
        remove_tree(home)


def _run_version(settings, emu_dir: Path) -> list[str]:
    tools = isolation.tool_paths(settings)
    inner = isolation.bwrap_argv(
        tools["bwrap"],
        emulator_dir=emu_dir,
        venv_dir=None,
        src_dir=settings.src_dir,
        ctl_dir=None,
        logs_dir=None,
        command=[f"{isolation.SANDBOX_EMULATOR}/bin/aws-emulator", "-v"],
    )
    argv = [tools["systemd-run"], "--user", "--scope", "--quiet"]
    for prop in ("CPUQuota=100%", "MemoryMax=1073741824", "MemorySwapMax=0", "RuntimeMaxSec=60"):
        argv += ["-p", prop]
    proc = subprocess.run(argv + ["--"] + inner, capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise LabError("install_invalid", "emulator version check failed inside the sandbox", {"stderr": proc.stderr.strip()[-300:]})
    return [line.strip() for line in proc.stdout.strip().splitlines()]


def _ensure_executable(settings, lock: dict, deb: Path) -> dict:
    ensure_private_dir(settings.data_dir)
    ensure_private_dir(settings.emulator_root)
    target = emulator_dir(settings, lock)
    exe = target / "bin" / "aws-emulator"
    if exe.exists():
        digest = sha256_file(exe)
        if digest == lock["executable"]["sha256"] and _run_version(settings, target) == lock["executable"]["version_output"]:
            return {"reused": True, "identity_verified": True}
        remove_tree(target)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=settings.emulator_root))
    try:
        subprocess.run(["nice", "-n", "10", "dpkg-deb", "-x", str(deb), str(staging)], check=True, capture_output=True, timeout=600)
        extracted = staging / "opt" / "aws-emulator"
        bin_path = extracted / "bin" / "aws-emulator"
        if not bin_path.is_file() or bin_path.is_symlink():
            raise LabError("install_invalid", "package payload lacks the emulator executable")
        if sha256_file(bin_path) != lock["executable"]["sha256"]:
            raise LabError("install_invalid", "extracted executable digest does not match the lock")
        for root, dirs, files in os.walk(extracted):
            for name in dirs + files:
                full = os.path.join(root, name)
                if not os.path.islink(full):
                    os.chmod(full, os.stat(full).st_mode & ~0o022)
        os.chmod(extracted, 0o755)  # noqa: S103 - read-only program directory, no write bits for others
        version_lines = _run_version(settings, extracted)
        if version_lines != lock["executable"]["version_output"]:
            raise LabError("install_invalid", "emulator version output differs from the lock", {"observed": version_lines})
        os.rename(extracted, target)
    finally:
        remove_tree(staging)
    return {"reused": False}


def _ensure_venv(settings, venv: Path, kind: str) -> dict:
    if _venv_current(venv, settings, kind):
        return {"reused": True}
    if venv.exists():
        remove_tree(venv)
    subprocess.run([sys.executable, "-I", "-m", "venv", str(venv)], check=True, capture_output=True, timeout=300)
    pip = [
        str(venv / "bin" / "python"),
        "-I",
        "-m",
        "pip",
        "install",
        "--require-hashes",
        "--no-deps",
        "--only-binary=:all:",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--quiet",
        "-r",
        str(_req_file(settings, kind)),
    ]
    proc = subprocess.run(pip, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise LabError("install_invalid", f"{kind} dependency installation failed", {"stderr": proc.stderr.strip()[-600:]})
    check = subprocess.run(
        [str(venv / "bin" / "python"), "-I", "-c", "import botocore; print(botocore.__version__)"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check.returncode != 0:
        raise LabError("install_invalid", f"{kind} venv cannot import botocore")
    (venv / ".lab-requirements.sha256").write_text(sha256_file(_req_file(settings, kind)) + "\n")
    return {"reused": False, "botocore": check.stdout.strip()}


def install(settings, lock_path: Path, *, dev: bool, offline: bool, dry_run: bool, extra_tracked: list[str]) -> dict:
    lock = load_lock(lock_path)
    result: dict = {"lock": {"version": lock["version"], "asset_sha256": lock["asset"]["sha256"]}}
    result["preflight"] = preflight(settings, lock)
    steps = plan(settings, lock, dev)
    result["plan"] = steps
    projected = _projected_bytes(settings, lock, dev)
    ensure_private_dir(settings.state_dir)
    ledger.init_ledger(settings, extra_tracked)
    result["admission"] = ledger.admit(settings, projected, "bootstrap")
    if dry_run:
        result["dry_run"] = True
        return result
    started = time.monotonic()
    deb = _ensure_asset(settings, lock, offline)
    result["signature"] = _verify_signature(settings, lock, deb)
    result["executable"] = _ensure_executable(settings, lock, deb)
    result["runtime_venv"] = _ensure_venv(settings, settings.runtime_venv, "runtime")
    if dev:
        result["dev_venv"] = _ensure_venv(settings, settings.dev_venv, "dev")
    exe = emulator_dir(settings, lock) / "bin" / "aws-emulator"
    st = exe.stat()
    receipt = {
        "schema": 1,
        "lab_version": __version__,
        "version": lock["version"],
        "asset_sha256": lock["asset"]["sha256"],
        "executable_sha256": lock["executable"]["sha256"],
        "executable_size": st.st_size,
        "executable_mtime_ns": st.st_mtime_ns,
        "executable_inode": st.st_ino,
        "signature_verified": result["signature"]["verified"],
        "version_output": lock["executable"]["version_output"],
        "runtime_requirements_sha256": requirements_sha256(settings),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    atomic_write_json(settings.receipt_path, receipt)
    result["receipt"] = {k: receipt[k] for k in ("version", "executable_sha256", "installed_at")}
    result["duration_seconds"] = round(time.monotonic() - started, 2)
    result["disk"] = ledger.measure(settings)
    return result


def install_dev_tools(settings) -> dict:
    """Only the hashed development toolchain, for static gates on standard CI
    runners. No emulator, no namespaces, no receipt: the lab stays unusable."""
    if sys.version_info < (3, 12):  # noqa: UP036 - bootstrap may run on an older system python
        raise LabError("preflight_failed", "Python 3.12 or newer is required", {"python": sys.version.split()[0]})
    ensure_private_dir(settings.data_dir)
    ensure_private_dir(settings.state_dir)
    ledger.init_ledger(settings, [])
    admission = ledger.admit(settings, 0 if _venv_current(settings.dev_venv, settings, "dev") else VENV_DEV_ESTIMATE, "dev bootstrap")
    return {"dev_venv": _ensure_venv(settings, settings.dev_venv, "dev"), "admission": admission, "emulator_installed": False}


def uninstall(settings) -> dict:
    active = []
    try:
        active = [u["unit"] for u in isolation.list_lab_units(settings)]
    except LabError:
        active = []
    records = list(settings.sandboxes_dir.glob("*/record.json")) if settings.sandboxes_dir.exists() else []
    if active or records:
        raise LabError(
            "fixture_state_conflict" if records else "slot_busy",
            "sandboxes still exist; destroy them before uninstalling",
            {"active_units": len(active), "records": len(records), "remedy": "aws-lab status; aws-lab destroy NAME"},
        )
    before = ledger.measure(settings)["locations"]
    removed = []
    for name, path in settings.owned_locations().items():
        if path.exists():
            remove_tree(path)
            removed.append(name)
    return {"removed_locations": removed, "freed_bytes": sum(before.values())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bootstrap", description="Install or uninstall ono-sendai-lab (no sudo).")
    parser.add_argument("--plan", action="store_true", help="preflight and print the plan without changing anything")
    parser.add_argument("--dev", action="store_true", help="also install the hashed development toolchain venv")
    parser.add_argument("--offline", action="store_true", help="use only cached downloads")
    parser.add_argument("--lock", default=None, help="alternative lock file (tests)")
    parser.add_argument("--uninstall", action="store_true", help="remove every lab-owned location")
    parser.add_argument("--dev-tools-only", action="store_true", help="only the hashed gate toolchain (standard CI runners)")
    parser.add_argument("--track", action="append", default=[], help="extra absolute path to count in the disk ledger")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    settings = load_settings()
    try:
        if args.uninstall:
            result = uninstall(settings)
        elif args.dev_tools_only:
            result = install_dev_tools(settings)
        else:
            lock_path = Path(args.lock).resolve() if args.lock else settings.lock_path
            result = install(settings, lock_path, dev=args.dev, offline=args.offline, dry_run=args.plan, extra_tracked=args.track)
        doc = {"schema_version": 1, "ok": True, "command": "bootstrap", "result": redact_obj(result)}
        code = 0
    except LabError as exc:
        doc = {"schema_version": 1, "ok": False, "command": "bootstrap", "error": exc.to_dict()}
        code = exc.exit_code
    except subprocess.CalledProcessError as exc:
        err = LabError("install_invalid", f"setup command failed: {Path(exc.cmd[0]).name}")
        doc = {"schema_version": 1, "ok": False, "command": "bootstrap", "error": err.to_dict()}
        code = err.exit_code
    except Exception as exc:  # noqa: BLE001 - report, never crash without a document
        err = LabError("internal_error", f"unexpected bootstrap failure: {type(exc).__name__}")
        doc = {"schema_version": 1, "ok": False, "command": "bootstrap", "error": err.to_dict()}
        code = err.exit_code
    if args.json:
        print(json.dumps(doc, sort_keys=True))
    elif doc["ok"]:
        res = doc["result"]
        if res.get("dry_run"):
            print("bootstrap plan (no changes made):")
            for step in res["plan"]:
                print(f"  - {step['step']}" + ("" if step.get("needed", True) else " (already satisfied)"))
        elif "emulator_installed" in res:
            print("installed the hashed development toolchain only (no emulator)")
        elif "removed_locations" in res:
            print(f"uninstalled: removed {', '.join(res['removed_locations']) or 'nothing'}")
        else:
            print(f"installed aws-emulator {res['lock']['version']} (sha256 {res['lock']['asset_sha256'][:16]}…); signature verified")
    else:
        print(f"bootstrap failed: {doc['error']['code']}: {doc['error']['message']}", file=sys.stderr)
        for key, value in doc["error"].get("details", {}).items():
            print(f"  {key}: {value}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
