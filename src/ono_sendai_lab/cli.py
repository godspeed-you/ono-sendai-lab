"""`aws-lab` command line (LAB-050, LAB-052; ADR-0007).

Noninteractive. ``--json`` (anywhere before ``--``) prints exactly one JSON
document on stdout; diagnostics go to stderr, redacted. Exit status classes
are defined in errors.py and docs/contracts/cli-envelope.schema.json.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import SCHEMA_VERSION, __version__
from .config import LOGS_DEFAULT_LINES, STARTUP_TIMEOUT_DEFAULT, load_settings
from .errors import LabError
from .redact import redact_obj, redact_text

COMMANDS = ("doctor", "create", "status", "credentials", "seed", "reset", "destroy", "extend", "logs", "scenarios", "aws")


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # never print usage and exit(2) behind our back
        raise LabError("usage_error", redact_text(message))

    def exit(self, status=0, message=None):
        if message:
            sys.stderr.write(message)
        raise SystemExit(status)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="aws-lab",
        description="Disposable, contained AWS Emulator sandboxes (ono-sendai-lab).",
        epilog="Global option: --json (anywhere before --) prints exactly one JSON document on stdout. "
        "Exit codes: 0 ok, 2 usage, 3 not found/conflict/stale, 4 refused, 5 upstream, 6 lifecycle/internal.",
    )
    p.add_argument("--version", action="store_true", help="print the lab version and exit")
    sub = p.add_subparsers(dest="command", parser_class=_Parser)
    sub.add_parser("doctor", help="preflight: versions, platform, isolation, resource controls, disk")
    c = sub.add_parser("create", help="create a sandbox with an empty, verified baseline")
    c.add_argument("--name", required=True)
    c.add_argument("--startup-timeout", type=int, default=STARTUP_TIMEOUT_DEFAULT, help="seconds, 1..60 (default 30)")
    c.add_argument("--backend", default="local", help="only 'local' is implemented")
    s = sub.add_parser("status", help="list sandboxes or show one")
    s.add_argument("name", nargs="?")
    cr = sub.add_parser("credentials", help="connection descriptor (references only, never secrets)")
    cr.add_argument("name")
    cr.add_argument("--generation")
    sd = sub.add_parser("seed", help="seed a scenario into an EMPTY fixture and verify it")
    sd.add_argument("name")
    sd.add_argument("--scenario", required=True)
    sd.add_argument("--generation")
    r = sub.add_parser("reset", help="replace the generation; empty baseline or --scenario")
    r.add_argument("name")
    r.add_argument("--scenario")
    r.add_argument("--startup-timeout", type=int, default=STARTUP_TIMEOUT_DEFAULT)
    d = sub.add_parser("destroy", help="stop and remove a sandbox (absent name succeeds)")
    d.add_argument("name")
    e = sub.add_parser("extend", help="extend the lease by N minutes, up to the absolute lifetime")
    e.add_argument("name")
    e.add_argument("--minutes", type=int, required=True)
    e.add_argument("--generation")
    lg = sub.add_parser("logs", help="bounded, redacted tail of lab-owned logs")
    lg.add_argument("name")
    lg.add_argument("--tail", type=int, default=LOGS_DEFAULT_LINES)
    sub.add_parser("scenarios", help="list scenarios and operation qualification")
    a = sub.add_parser("aws", help="run one allowlisted AWS operation in the managed context")
    a.add_argument("name")
    a.add_argument("--generation")
    return p


def _split(argv: list[str]) -> tuple[list[str], list[str], bool]:
    """Separate lab arguments from the AWS tokens after ``--``; pull out --json."""
    if "--" in argv:
        idx = argv.index("--")
        head, tail = argv[:idx], argv[idx + 1 :]
    else:
        head, tail = argv, []
    as_json = "--json" in head
    head = [a for a in head if a != "--json"]
    return head, tail, as_json


def run(argv: list[str]) -> tuple[dict, int]:
    head, aws_tokens, as_json = _split(argv)
    parser = build_parser()
    if as_json and ("-h" in head or "--help" in head):
        return {"command": "help", "result": {"usage": parser.format_help(), "commands": list(COMMANDS)}}, 0
    args = parser.parse_args(head)
    if args.version:
        return {"command": "version", "result": {"lab_version": __version__, "schema_version": SCHEMA_VERSION}}, 0
    if args.command is None:
        raise LabError("usage_error", "a command is required", {"commands": list(COMMANDS)})
    if args.command != "aws" and aws_tokens:
        raise LabError("usage_error", "'--' is only valid for the aws command")
    from .lifecycle import Lab

    lab = Lab(load_settings())
    cmd = args.command
    if cmd == "doctor":
        report = lab.doctor()
        if not report["safe"]:
            code = "preflight_failed"
            iso = report["checks"].get("network_file_isolation", {})
            rc = report["checks"].get("resource_controls", {})
            if not iso.get("ok"):
                code = "isolation_unavailable"
            elif not rc.get("ok"):
                code = "resource_control_unavailable"
            raise LabError(code, "the local backend is unsafe or unavailable on this host", report)
        result = report
    elif cmd == "create":
        result = lab.create(args.name, startup_timeout=args.startup_timeout, backend=args.backend)
    elif cmd == "status":
        result = lab.status(args.name)
    elif cmd == "credentials":
        result = lab.credentials(args.name, args.generation)
    elif cmd == "seed":
        result = lab.seed(args.name, args.scenario, args.generation)
    elif cmd == "reset":
        result = lab.reset(args.name, args.scenario, startup_timeout=args.startup_timeout)
    elif cmd == "destroy":
        result = lab.destroy(args.name)
    elif cmd == "extend":
        result = lab.extend(args.name, args.minutes, args.generation)
    elif cmd == "logs":
        result = lab.logs(args.name, args.tail)
    elif cmd == "scenarios":
        result = lab.scenarios()
    elif cmd == "aws":
        if not aws_tokens:
            raise LabError("usage_error", "usage: aws-lab aws NAME -- SERVICE OPERATION [ARGS...]")
        result = lab.aws(args.name, aws_tokens, args.generation)
    else:  # pragma: no cover - argparse rejects unknown commands
        raise LabError("usage_error", f"unknown command {cmd!r}")
    return {"command": cmd, "result": result}, 0


def _human(doc: dict) -> str:
    cmd, res = doc["command"], doc["result"]
    if cmd == "version":
        return f"aws-lab {res['lab_version']}"
    if cmd == "doctor":
        iso = res["checks"].get("network_file_isolation", {})
        rc = res["checks"].get("resource_controls", {})
        inst = res["checks"].get("install", {})
        return (
            f"doctor: safe (emulator {inst.get('version')}, digest verified; "
            f"namespace interfaces {iso.get('interfaces')}; limits {rc.get('limits', {}).get('cpu.max')} cpu, "
            f"{rc.get('limits', {}).get('memory.max')} bytes memory, swap {rc.get('limits', {}).get('memory.swap.max')})"
        )
    if cmd == "status" and "sandboxes" in res:
        if not res["sandboxes"]:
            return "no sandboxes"
        return "\n".join(
            f"{s['name']}\t{s['state']}\tfixture={s['fixture']['state']}\tgeneration={s['generation']['number']}\tlease={s['generation']['lease_deadline']}"
            for s in res["sandboxes"]
        )
    if cmd in ("create", "status", "reset") and "state" in res:
        gen = res["generation"]
        line = (
            f"{res['name']}: {res['state']} fixture={res['fixture']['state']} "
            f"generation={gen['number']} ({(gen['id'] or '')[:12]}) lease until {gen['lease_deadline']}"
        )
        if res["fixture"].get("scenario"):
            line += f" scenario={res['fixture']['scenario']}"
        return line
    if cmd == "destroy":
        return f"{res['name']}: destroyed" if res["destroyed"] else f"{res['name']}: absent (nothing to do)"
    if cmd == "seed":
        return f"{res['name']}: {res['scenario']} VERIFIED ({res['verification']['assertions']} assertions) mapping={res['mapping']}"
    if cmd == "extend":
        return f"{res['name']}: lease until {res['lease_deadline']} (absolute limit {res['max_deadline']})"
    if cmd == "logs":
        body = "\n".join(res["lines"])
        return body + (f"\n[truncated: showing {res['returned']} of {res['available']} lines]" if res["truncated"] else "")
    if cmd == "scenarios":
        return "\n".join(f"{s['id']} v{s['version']}: {', '.join(s['resources']) or 'no resources'}" for s in res["scenarios"])
    return json.dumps(res, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _, _, as_json = _split(argv)
    command = next((a for a in _split(argv)[0] if a in COMMANDS), None)
    try:
        doc, code = run(argv)
        envelope = {"schema_version": SCHEMA_VERSION, "ok": True, "command": doc["command"], "result": redact_obj(doc["result"])}
    except LabError as exc:
        envelope = {"schema_version": SCHEMA_VERSION, "ok": False, "command": command or "aws-lab", "error": exc.to_dict()}
        code = exc.exit_code
    except SystemExit as exc:  # --help
        return int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001 - always emit one document
        err = LabError("internal_error", f"unexpected failure: {type(exc).__name__}")
        envelope = {"schema_version": SCHEMA_VERSION, "ok": False, "command": command or "aws-lab", "error": err.to_dict()}
        code = err.exit_code
    if as_json:
        sys.stdout.write(json.dumps(envelope, sort_keys=True) + "\n")
    elif envelope["ok"]:
        sys.stdout.write(redact_text(_human({"command": envelope["command"], "result": envelope["result"]})) + "\n")
    else:
        err = envelope["error"]
        sys.stderr.write(f"aws-lab: {err['code']}: {err['message']}\n")
        remedy = err.get("details", {}).get("remedy")
        if remedy:
            sys.stderr.write(f"  remedy: {remedy}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
