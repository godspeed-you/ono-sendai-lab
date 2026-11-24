"""In-sandbox supervisor and managed worker (LAB-024..027, LAB-033/034, LAB-043).

Runs as the main process of the transient unit, inside bubblewrap. It
verifies containment, starts the emulator, proves TLS health, enforced
authentication, telemetry-off, the signed STS identity and the region
context, then serves the private worker channel. A watchdog thread enforces
the lease, the absolute lifetime, activation and emulator liveness even when
no CLI is running; systemd's RuntimeMaxSec is the outer backstop.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import selectors
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import ipc, operations, scenarios, selfcheck
from .awsclient import ManagedClient
from .errors import LabError
from .fsutil import atomic_write_json
from .redact import redact_obj, redact_text

CTL = Path("/run/lab/ctl")
LOGS = Path("/run/lab/logs")
HOME = Path("/home/lab")
EMULATOR = "/opt/aws-emulator/bin/aws-emulator"
LOG_FIELDS = {"endpoint_url", "health_check_url"}

EXIT_STOPPED = 0
EXIT_EMULATOR_DIED = 70
EXIT_STARTUP_FAILED = 71
EXIT_ISOLATION_FAILED = 72
EXIT_NOT_ACTIVATED = 73
EXIT_LEASE_EXPIRED = 75
EXIT_MAX_LIFETIME = 76
EXIT_IDLE_TIMEOUT = 77


class RingLog:
    """Size-bounded log: one active file plus one rotated predecessor."""

    def __init__(self, path: Path, max_bytes: int):
        self.path = path
        self.max_bytes = max_bytes
        self.lock = threading.Lock()

    def write(self, kind: str, text: str) -> None:
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {kind} {redact_text(text)[:600]}\n"
        with self.lock:
            try:
                if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
                    os.replace(self.path, self.path.with_name(self.path.name + ".1"))
                with open(self.path, "a") as fh:
                    fh.write(line)
            except OSError:
                pass


def sanitize_emulator_line(line: str) -> str:
    """Keep only stable, non-sensitive fields of an upstream JSON log line."""
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return json.dumps({"unparsed_bytes": len(line)})
    fields = obj.get("fields", {}) if isinstance(obj, dict) else {}
    keep = {
        "timestamp": obj.get("timestamp"),
        "level": obj.get("level"),
        "target": obj.get("target"),
        "message": str(fields.get("message", ""))[:300],
    }
    for key in LOG_FIELDS & set(fields):
        keep[key] = fields[key]
    dropped = sorted(set(fields) - LOG_FIELDS - {"message"})
    if dropped:
        keep["dropped_fields"] = len(dropped)
    return json.dumps(redact_obj(keep), sort_keys=True)


class Supervisor:
    def __init__(self, args):
        self.args = args
        self.generation = args.generation
        self.log = RingLog(LOGS / "lab.log", args.log_ring_bytes)
        self.lock = threading.RLock()
        self.exit_lock = threading.Lock()
        self.lease_deadline = float(args.lease_deadline)
        self.max_deadline = float(args.max_deadline)
        self.activation_deadline = time.time() + args.startup_timeout + args.activation_grace
        self.activated = False
        self.emulator: subprocess.Popen | None = None
        self.client: ManagedClient | None = None
        self.idle_seen = False
        self.startup: dict = {}
        self.status: dict = {"generation": self.generation, "phase": "starting", "supervisor_pid": os.getpid()}
        self.telemetry_notice = threading.Event()
        self.telemetry_text = ""
        self.exiting = threading.Event()
        self.started_monotonic = time.monotonic()
        self.port_blocker: socket.socket | None = None

    # -- status ----------------------------------------------------------
    def write_status(self, **changes) -> None:
        self.status.update(changes)
        self.status["updated"] = time.time()
        atomic_write_json(CTL / "status.json", redact_obj(self.status))

    def finish(self, code: int, reason: str, detail: str = "") -> None:
        """Terminate the emulator, record why, exit. Idempotent."""
        with self.exit_lock:
            if self.exiting.is_set():
                return
            self.exiting.set()
        self.log.write("LAB", f"supervisor exiting: reason={reason} {detail}")
        emu = self.emulator
        if emu is not None and emu.poll() is None:
            emu.terminate()
            try:
                emu.wait(10)
            except subprocess.TimeoutExpired:
                emu.kill()
                emu.wait(5)
        exit_doc = {
            "generation": self.generation,
            "reason": reason,
            "detail": redact_text(detail)[:500],
            "exit_code": code,
            "emulator_returncode": emu.returncode if emu is not None else None,
            "time": time.time(),
        }
        try:
            atomic_write_json(CTL / "exit.json", exit_doc)
            self.write_status(phase="exited", exit_reason=reason)
            sock = CTL / "worker.sock"
            if sock.exists():
                sock.unlink()
        except OSError:
            pass
        os._exit(code)

    # -- startup ---------------------------------------------------------
    def _pump_stderr(self, stream) -> None:
        for raw in stream:
            line = raw.rstrip("\n")
            if "telemetry" in line.lower() and not self.telemetry_notice.is_set():
                self.telemetry_text = line
                self.telemetry_notice.set()
            self.log.write("EMU-STDERR", line[:300])

    def _pump_stdout(self, stream, startup_holder: dict, ready: threading.Event) -> None:
        for raw in stream:
            line = raw.rstrip("\n")
            sanitized = sanitize_emulator_line(line)
            self.log.write("EMU", sanitized)
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = obj.get("fields", {}).get("message", "") if isinstance(obj, dict) else ""
            if message == "emulator server information" and not ready.is_set():
                startup_holder.update(obj.get("fields", {}))
                ready.set()
            if "idle-timeout" in message or "idle timeout" in message:
                self.idle_seen = True

    def start(self) -> None:
        report = selfcheck.report()
        self.startup["containment"] = {
            k: report[k] for k in ("ok", "interfaces", "ipv4_routes", "external_probe", "cap_eff", "no_new_privs")
        }
        if not report["ok"]:
            self.write_status(phase="failed", reason="isolation", problems=report["problems"])
            self.finish(EXIT_ISOLATION_FAILED, "isolation-failed", "; ".join(report["problems"]))
        cfg_dir = HOME / ".aws" / "emulator"
        cfg_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        (cfg_dir / "config.toml").write_text("[telemetry]\nenabled = false\n")
        port = "0"
        if self.args.occupy_port:
            self.port_blocker = socket.socket()
            self.port_blocker.bind(("127.0.0.1", 0))
            self.port_blocker.listen(1)
            port = str(self.port_blocker.getsockname()[1])
        argv = [
            EMULATOR,
            "--bind",
            "127.0.0.1",
            "--port",
            port,
            "--auth",
            "on",
            "--telemetry",
            "off",
            "--idle-timeout",
            str(self.args.idle_seconds),
        ]
        env = {"HOME": str(HOME), "PATH": "/usr/bin", "LANG": "C.UTF-8", "EMULATOR_TELEMETRY": "off"}
        self.log.write("LAB", f"starting emulator generation={self.generation}")
        self.emulator = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(HOME)
        )
        holder: dict = {}
        ready = threading.Event()
        threading.Thread(target=self._pump_stdout, args=(self.emulator.stdout, holder, ready), daemon=True).start()
        threading.Thread(target=self._pump_stderr, args=(self.emulator.stderr,), daemon=True).start()
        deadline = time.monotonic() + self.args.startup_timeout
        while not ready.is_set():
            if self.emulator.poll() is not None:
                time.sleep(0.2)
                self.write_status(phase="failed", reason="emulator-exited-during-startup", emulator_returncode=self.emulator.returncode)
                self.finish(EXIT_STARTUP_FAILED, "startup-failed", f"emulator exited with {self.emulator.returncode} before readiness")
            if time.monotonic() > deadline:
                self.write_status(phase="failed", reason="startup-timeout")
                self.finish(EXIT_STARTUP_FAILED, "startup-failed", "emulator did not report readiness within the startup timeout")
            ready.wait(0.1)
        try:
            self._verify_started(holder)
        except LabError as exc:
            self.write_status(phase="failed", reason=exc.code, message=exc.message)
            self.finish(EXIT_STARTUP_FAILED, "startup-failed", exc.message)

    def _verify_started(self, info: dict) -> None:
        endpoint = str(info.get("endpoint_url", ""))
        prefix = "https://127.0.0.1:"
        if not endpoint.startswith(prefix) or not endpoint[len(prefix) :].isdigit():
            raise LabError("startup_failed", "emulator endpoint is not https on 127.0.0.1")
        port = endpoint[len(prefix) :]
        base = HOME / ".aws" / "emulator" / port
        ca_file = base / "ca.pem"
        cred_file = base / "emulator_credentials"
        if str(info.get("ca_file")) != str(ca_file):
            raise LabError("startup_failed", "emulator CA path differs from the private HOME layout")
        if not self.telemetry_notice.wait(5) or "disabled" not in self.telemetry_text:
            raise LabError("startup_failed", "telemetry could not be verified as disabled")
        for path in (ca_file, cred_file):
            if (path.stat().st_mode & 0o077) != 0:
                raise LabError("startup_failed", "emulator runtime file is not owner-private")
        ctx = ssl.create_default_context(cafile=str(ca_file))
        try:
            with urllib.request.urlopen(f"{endpoint}/__admin/health", context=ctx, timeout=5) as resp:  # noqa: S310 - https only
                body = resp.read(64).decode()
                if resp.status != 200 or body.strip() != "ok":
                    raise LabError("startup_failed", "health route did not return ok")
        except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
            raise LabError("startup_failed", f"verified TLS health check failed: {type(exc).__name__}") from exc
        unsigned_status = None
        req = urllib.request.Request(  # noqa: S310 - fixed https endpoint parsed above
            endpoint + "/",
            data=b"Action=GetCallerIdentity&Version=2011-06-15",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:  # noqa: S310 - https only
                unsigned_status = resp.status
        except urllib.error.HTTPError as exc:
            unsigned_status = exc.code
        if unsigned_status not in (403, 404):
            raise LabError("startup_failed", "emulator accepted an unsigned request; authentication is not enforced")
        parser = configparser.ConfigParser()
        parser.read(cred_file)
        if not parser.sections():
            raise LabError("startup_failed", "emulator credentials file is missing")
        section = parser[parser.sections()[0]]
        access, secret = section.get("aws_access_key_id", ""), section.get("aws_secret_access_key", "")
        self.client = ManagedClient(endpoint=endpoint, region=self.args.region, ca_file=str(ca_file), access_key=access, secret_key=secret)
        ident = self.client.call("sts", "GetCallerIdentity", {}, mutating=False)
        if ident.get("Account") != self.args.expect_account or ident.get("Arn") != self.args.expect_arn:
            raise LabError(
                "startup_failed", "signed STS identity does not match the qualified emulator identity", {"account": ident.get("Account")}
            )
        zones = self.client.call("ec2", "DescribeAvailabilityZones", {}, mutating=False).get("AvailabilityZones", [])
        regions = sorted({z.get("RegionName") for z in zones})
        if regions != [self.args.region]:
            raise LabError("startup_failed", "region context does not match", {"observed": regions})
        ca_sha = hashlib.sha256(ca_file.read_bytes()).hexdigest()
        self.startup.update(
            {
                "endpoint": endpoint,
                "port": int(port),
                "ca_ref": f"managed:{ca_file}",
                "credentials_ref": f"managed:{cred_file}",
                "config_ref": f"managed:{base / 'config'}",
                "ca_sha256": ca_sha,
                "access_key_fingerprint": hashlib.sha256(access.encode()).hexdigest()[:16],
                "identity": {"account": ident.get("Account"), "arn": ident.get("Arn")},
                "region": self.args.region,
                "zones": len(zones),
                "auth": {"mode": "sigv4", "unsigned_request_status": unsigned_status},
                "telemetry": "disabled",
                "tls": "verified-with-run-ca",
                "emulator_pid": self.emulator.pid,
            }
        )
        self.write_status(phase="ready", startup=self.startup)
        self.log.write("LAB", f"emulator ready on 127.0.0.1:{port} (isolated namespace)")

    # -- watchdog --------------------------------------------------------
    def watchdog(self) -> None:
        while not self.exiting.is_set():
            now = time.time()
            emu = self.emulator
            if emu is not None and emu.poll() is not None:
                if emu.returncode == 0 and self.idle_seen:
                    self.finish(EXIT_IDLE_TIMEOUT, "idle-timeout", "emulator stopped after the idle timeout")
                self.finish(EXIT_EMULATOR_DIED, "emulator-died", f"emulator exited unexpectedly with {emu.returncode}")
            if not self.activated and now > self.activation_deadline:
                self.finish(EXIT_NOT_ACTIVATED, "not-activated", "the lifecycle manager never activated this generation")
            if now >= self.max_deadline:
                self.finish(EXIT_MAX_LIFETIME, "max-lifetime", "absolute lifetime reached")
            if now >= self.lease_deadline:
                self.finish(EXIT_LEASE_EXPIRED, "lease-expired", "lease deadline reached")
            time.sleep(0.25)

    # -- worker channel --------------------------------------------------
    def serve(self) -> None:
        path = CTL / "worker.sock"
        if path.exists():
            path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)
        try:
            server.bind(str(path))
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        server.listen(4)
        sel = selectors.DefaultSelector()
        sel.register(server, selectors.EVENT_READ)
        while not self.exiting.is_set():
            for _key, _ in sel.select(timeout=0.5):
                conn, _ = server.accept()
                conn.settimeout(30)
                try:
                    self._handle(conn)
                except Exception as exc:  # noqa: BLE001 - never let one request kill the worker
                    self.log.write("LAB", f"request handling error: {type(exc).__name__}")
                finally:
                    conn.close()

    def _handle(self, conn: socket.socket) -> None:
        if ipc.peer_uid(conn) != os.getuid():
            return
        try:
            request = ipc.recv_frame(conn, ipc.MAX_REQUEST)
        except (ValueError, ConnectionError, json.JSONDecodeError):
            ipc.send_frame(conn, {"ok": False, "error": {"code": "invalid_argument", "message": "malformed request"}}, ipc.MAX_RESPONSE)
            return
        try:
            if request.get("v") != ipc.PROTOCOL:
                raise LabError("invalid_argument", "unsupported protocol version")
            if request.get("generation") != self.generation:
                raise LabError("stale_generation", "request names a different generation", {"current_generation_known": True})
            op = request.get("op")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise LabError("invalid_argument", "params must be an object")
            handler = getattr(self, "op_" + str(op).replace("-", "_"), None)
            if handler is None or str(op).startswith("_"):
                raise LabError("operation_not_allowed", "unknown worker operation")
            if op == "probe" and not self.args.test_hooks:
                raise LabError("operation_not_allowed", "probe requires a test-mode sandbox")
            with self.lock:
                if self.exiting.is_set():
                    raise LabError("not_ready", "generation is stopping")
                result = handler(params)
            response = {"ok": True, "result": redact_obj(result)}
        except LabError as exc:
            response = {"ok": False, "error": exc.to_dict()}
        data = json.dumps(response, default=str)
        if len(data) > ipc.MAX_RESPONSE:
            response = {"ok": False, "error": LabError("upstream_error", "response exceeds the channel bound").to_dict()}
        ipc.send_frame(conn, response, ipc.MAX_RESPONSE)

    def _require_ready(self) -> ManagedClient:
        if not self.activated or self.client is None:
            raise LabError("not_ready", "generation is not active")
        return self.client

    def op_ping(self, _params):
        emu = self.emulator
        return {
            "generation": self.generation,
            "activated": self.activated,
            "emulator_alive": emu is not None and emu.poll() is None,
            "lease_deadline": self.lease_deadline,
            "max_deadline": self.max_deadline,
            "uptime_seconds": round(time.monotonic() - self.started_monotonic, 1),
        }

    def op_activate(self, params):
        self._set_lease(params)
        self.activated = True
        self.log.write("LAB", "generation activated by lifecycle manager")
        return {"activated": True, "startup": self.startup}

    def _set_lease(self, params):
        deadline = params.get("lease_deadline")
        if not isinstance(deadline, (int, float)):
            raise LabError("invalid_argument", "lease_deadline must be a number")
        if deadline > self.max_deadline:
            raise LabError("lease_cap_exceeded", "lease cannot exceed the absolute lifetime")
        self.lease_deadline = float(deadline)

    def op_set_lease(self, params):
        self._require_ready()
        self._set_lease(params)
        return {"lease_deadline": self.lease_deadline}

    def op_describe(self, _params):
        return {"startup": self.startup, "activated": self.activated}

    def op_aws(self, params):
        client = self._require_ready()
        tokens = params.get("tokens")
        if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens) or len(tokens) > 64:
            raise LabError("invalid_argument", "tokens must be a short list of strings")
        op, api_params = operations.parse_cli(tokens)
        if op.cli_name == "create-bucket":
            api_params = scenarios.bucket_create_params(api_params["Bucket"], client.region)
        self.log.write("LAB", f"aws {op.service} {op.cli_name}")
        response = client.call(op.service, op.api_name, api_params, mutating=op.mutating)
        return {"service": op.service, "operation": op.cli_name, "mutating": op.mutating, "response": response}

    def op_baseline(self, _params):
        return scenarios.observe_baseline(self._require_ready())

    def op_seed(self, params):
        client = self._require_ready()
        return scenarios.seed(client, params.get("manifest"), params.get("labels", {}), fault=self.args.seed_fault)

    def op_verify(self, params):
        client = self._require_ready()
        return scenarios.verify(
            client, params.get("manifest"), params.get("labels", {}), params.get("mapping", {}), params.get("baseline", {})
        )

    def op_metrics(self, _params):
        def proc_stats(pid: int | None) -> dict:
            if pid is None:
                return {}
            out: dict = {}
            try:
                for line in open(f"/proc/{pid}/status"):
                    if line.startswith(("VmRSS:", "VmHWM:")):
                        out[line.split(":")[0]] = int(line.split()[1]) * 1024
                fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
                tick = os.sysconf("SC_CLK_TCK")
                out["cpu_seconds"] = round((int(fields[11]) + int(fields[12])) / tick, 2)
            except (OSError, IndexError, ValueError):
                pass
            return out

        emu = self.emulator
        return {
            "emulator": proc_stats(emu.pid if emu is not None and emu.poll() is None else None),
            "supervisor": proc_stats(os.getpid()),
        }

    def op_probe(self, params):
        """Test-mode only: read-only containment and negative-path probes."""
        results = []
        for check in params.get("checks", [])[:20]:
            kind = check.get("kind")
            if kind == "tcp":
                sock = socket.socket()
                sock.settimeout(2)
                try:
                    sock.connect((str(check["host"]), int(check["port"])))
                    outcome = "connected"
                except OSError as exc:
                    outcome = type(exc).__name__ + (f":{exc.errno}" if exc.errno else "")
                finally:
                    sock.close()
            elif kind == "abstract_unix":
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    sock.connect("\0" + str(check["name"]))
                    outcome = "connected"
                except OSError as exc:
                    outcome = type(exc).__name__
                finally:
                    sock.close()
            elif kind == "path_exists":
                outcome = os.path.lexists(str(check["path"]))
            elif kind == "env_names":
                outcome = sorted(os.environ)
            elif kind == "selfcheck":
                outcome = selfcheck.report()
            elif kind in ("wrong_credentials", "wrong_ca", "unreachable_endpoint"):
                client = self._require_ready()
                overrides: dict = {}
                if kind == "wrong_credentials":
                    overrides["keys"] = ("EMULATOR000000000000", "0" * 40)
                elif kind == "wrong_ca":
                    overrides["ca_file"] = "/usr/share/ca-certificates/mozilla/ISRG_Root_X1.crt"
                else:
                    overrides["endpoint"] = "https://127.0.0.1:1"
                try:
                    client.call("sts", "GetCallerIdentity", {}, mutating=False, **overrides)
                    outcome = {"ok": True}
                except LabError as exc:
                    outcome = {"ok": False, "code": exc.code, "aws_code": exc.details.get("aws_code"), "reason": exc.details.get("reason")}
            else:
                outcome = "unknown-check"
            results.append({"check": check, "outcome": outcome})
        return {"results": results}


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ono-sendai-lab-supervisor")
    p.add_argument("--generation", required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--startup-timeout", type=int, required=True)
    p.add_argument("--activation-grace", type=int, required=True)
    p.add_argument("--lease-deadline", type=float, required=True)
    p.add_argument("--max-deadline", type=float, required=True)
    p.add_argument("--idle-seconds", type=int, required=True)
    p.add_argument("--log-ring-bytes", type=int, required=True)
    p.add_argument("--expect-account", required=True)
    p.add_argument("--expect-arn", required=True)
    p.add_argument("--test-hooks", action="store_true")
    p.add_argument("--occupy-port", action="store_true")
    p.add_argument("--seed-fault", default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    sup = Supervisor(args)

    def on_term(_signum, _frame):
        threading.Thread(target=sup.finish, args=(EXIT_STOPPED, "stop-requested"), daemon=True).start()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    sup.write_status(phase="starting")
    sup.start()
    threading.Thread(target=sup.watchdog, daemon=True).start()
    sup.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
