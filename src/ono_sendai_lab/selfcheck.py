"""In-namespace containment self-check (LAB-023, LAB-027, LAB-043).

Runs *inside* the sandbox (doctor probe and every supervisor start). Stdlib
only. It inspects kernel-visible state; it trusts nothing the host told it.
"""

from __future__ import annotations

import errno
import json
import os
import socket

# TEST-NET-1 (RFC 5737): documentation range, never routed to a real service.
EXTERNAL_PROBE = ("192.0.2.1", 9)
SENSITIVE_PATHS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/run/user",
    "/run/dbus",
    "/root",
    "/etc/passwd",
    "/etc/ssh",
    "/var/lib",
    "/mnt",
    "/media",
)
INERT_AWS_FILE = "/nonexistent/ono-sendai-lab/aws-config"
PROXY_VARS = {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy"}
ALLOWED_ENV = {
    "HOME",
    "PATH",
    "LANG",
    "EMULATOR_TELEMETRY",
    "PYTHONDONTWRITEBYTECODE",
    "AWS_EC2_METADATA_DISABLED",
    "AWS_LAB_SANDBOX",
    "PWD",
    # set by the managed client itself (awsclient.harden_environment) to inert values
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_DISABLE_HOST_PREFIX_INJECTION",
}


def interfaces() -> list[str]:
    names = []
    with open("/proc/net/dev") as fh:
        for line in fh.readlines()[2:]:
            names.append(line.split(":", 1)[0].strip())
    return sorted(names)


def ipv4_routes() -> list[str]:
    with open("/proc/net/route") as fh:
        return [line.split()[0] + ":" + line.split()[1] for line in fh.readlines()[1:] if line.strip()]


def ipv6_routes_non_lo() -> list[str]:
    try:
        with open("/proc/net/ipv6_route") as fh:
            return [line.split()[-1] for line in fh if line.split() and line.split()[-1] != "lo"]
    except FileNotFoundError:
        return []


def status_fields() -> dict[str, str]:
    fields = {}
    with open("/proc/self/status") as fh:
        for line in fh:
            key, _, value = line.partition(":")
            if key in ("Uid", "Gid", "CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Seccomp"):
                fields[key] = value.strip()
    return fields


def external_connect() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect(EXTERNAL_PROBE)
        return "connected"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno)) if exc.errno else type(exc).__name__
    finally:
        sock.close()


def mounts() -> list[dict[str, str]]:
    result = []
    with open("/proc/self/mountinfo") as fh:
        for line in fh:
            parts = line.split()
            sep = parts.index("-")
            result.append({"target": parts[4], "fstype": parts[sep + 1], "options": parts[5]})
    return result


def report() -> dict:
    problems: list[str] = []
    ifaces = interfaces()
    if ifaces != ["lo"]:
        problems.append(f"unexpected network interfaces: {ifaces}")
    routes4 = ipv4_routes()
    if routes4:
        problems.append(f"IPv4 routes present: {routes4}")
    routes6 = ipv6_routes_non_lo()
    if routes6:
        problems.append(f"non-loopback IPv6 routes present: {sorted(set(routes6))}")
    ext = external_connect()
    if ext not in ("ENETUNREACH", "EHOSTUNREACH"):
        problems.append(f"external probe returned {ext}, expected ENETUNREACH")
    st = status_fields()
    if st.get("CapEff", "").strip("0"):
        problems.append("effective capabilities are not empty")
    if st.get("NoNewPrivs") != "1":
        problems.append("no_new_privs is not set")
    env_names = sorted(os.environ)
    proxies = [n for n in env_names if n.lower() in PROXY_VARS]
    if proxies:
        problems.append(f"proxy variables present: {proxies}")
    unexpected_env = [n for n in env_names if n not in ALLOWED_ENV]
    if unexpected_env:
        problems.append(f"unexpected environment variables: {unexpected_env}")
    inert = {"AWS_CONFIG_FILE": INERT_AWS_FILE, "AWS_SHARED_CREDENTIALS_FILE": INERT_AWS_FILE, "AWS_DISABLE_HOST_PREFIX_INJECTION": "true"}
    wrong = [n for n, v in inert.items() if n in os.environ and os.environ[n] != v]
    if wrong:
        problems.append(f"lab-controlled variables carry unexpected values: {wrong}")
    visible = [p for p in SENSITIVE_PATHS if os.path.lexists(p)]
    if visible:
        problems.append(f"sensitive host paths visible: {visible}")
    home_entries = sorted(os.listdir("/home")) if os.path.isdir("/home") else []
    if home_entries not in ([], ["lab"]):
        problems.append(f"/home exposes entries: {home_entries}")
    mount_list = mounts()
    home_mount = [m for m in mount_list if m["target"] == os.environ.get("HOME", "/home/lab")]
    if not home_mount or home_mount[-1]["fstype"] != "tmpfs":
        problems.append("HOME is not a private tmpfs")
    writable = [
        m["target"]
        for m in mount_list
        if m["options"].split(",")[0] == "rw"
        and m["fstype"] not in ("tmpfs", "proc", "devtmpfs", "mqueue", "devpts")
        and m["target"] not in ("/run/lab/ctl", "/run/lab/logs")
    ]
    if writable:
        problems.append(f"unexpected writable mounts: {writable}")
    return {
        "ok": not problems,
        "problems": problems,
        "interfaces": ifaces,
        "ipv4_routes": routes4,
        "external_probe": {"target": f"{EXTERNAL_PROBE[0]}:{EXTERNAL_PROBE[1]}", "result": ext},
        "uid": st.get("Uid"),
        "cap_eff": st.get("CapEff"),
        "no_new_privs": st.get("NoNewPrivs"),
        "seccomp": st.get("Seccomp"),
        "env_names": env_names,
        "mount_targets": sorted({m["target"] for m in mount_list}),
    }


def main() -> int:
    result = report()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
