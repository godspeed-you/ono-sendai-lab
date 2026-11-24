"""Explicit AWS operation allowlist and argument grammar (LAB-050, LAB-051).

Machine-readable mirror: docs/contracts/aws-operations.json (checked by the
contract tests). Stdlib only; the host CLI and the in-sandbox worker parse
with this same module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import LabError

BUCKET_RE = re.compile(r"^(?!xn--)(?!.*\.\.)(?!\d+\.\d+\.\d+\.\d+$)[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
ID_RES = {
    "vpc_id": re.compile(r"^vpc-[0-9a-f]{8,32}$"),
    "subnet_id": re.compile(r"^subnet-[0-9a-f]{8,32}$"),
    "group_id": re.compile(r"^sg-[0-9a-f]{8,32}$"),
}
FILTER_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
TAG_KEY_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,64}$")
FILTER_NAMES = {
    "describe-vpcs": {"vpc-id", "cidr-block", "cidr", "is-default", "tag-key", "state"},
    "describe-subnets": {"vpc-id", "subnet-id", "cidr-block", "default-for-az", "tag-key", "state", "availability-zone"},
    "describe-security-groups": {"vpc-id", "group-id", "group-name", "tag-key"},
}
FORBIDDEN_OPTIONS = {
    "--endpoint-url",
    "--endpoint",
    "--profile",
    "--region",
    "--ca-bundle",
    "--no-verify-ssl",
    "--no-sign-request",
    "--cli-input-json",
    "--cli-input-yaml",
    "--generate-cli-skeleton",
    "--output",
    "--query",
    "--debug",
    "--cli-read-timeout",
    "--cli-connect-timeout",
    "--color",
    "--no-paginate",
    "--page-size",
    "--max-items",
    "--starting-token",
    "--proxy",
    "--access-key",
    "--secret-key",
    "--session-token",
    "--outfile",
}
FORBIDDEN_VALUE_PREFIXES = ("file://", "fileb://", "http://", "https://", "s3://")
MAX_LIST = 20
MAX_FILTERS = 5
MAX_FILTER_VALUES = 10


@dataclass(frozen=True)
class Param:
    flag: str
    api_name: str
    kind: str  # bucket | vpc_id | subnet_id | group_id | filters | cidr | name | description
    required: bool = False
    many: bool = False


@dataclass(frozen=True)
class Operation:
    service: str
    cli_name: str
    api_name: str
    mutating: bool
    public: bool
    params: tuple[Param, ...] = field(default_factory=tuple)


def _ops() -> dict[tuple[str, str], Operation]:
    ops = [
        Operation("sts", "get-caller-identity", "GetCallerIdentity", False, True),
        Operation("s3", "list-buckets", "ListBuckets", False, True),
        Operation("s3", "create-bucket", "CreateBucket", True, True, (Param("--bucket", "Bucket", "bucket", required=True),)),
        Operation("s3", "delete-bucket", "DeleteBucket", True, True, (Param("--bucket", "Bucket", "bucket", required=True),)),
        Operation(
            "ec2",
            "describe-vpcs",
            "DescribeVpcs",
            False,
            True,
            (Param("--vpc-ids", "VpcIds", "vpc_id", many=True), Param("--filters", "Filters", "filters", many=True)),
        ),
        Operation(
            "ec2",
            "describe-subnets",
            "DescribeSubnets",
            False,
            True,
            (Param("--subnet-ids", "SubnetIds", "subnet_id", many=True), Param("--filters", "Filters", "filters", many=True)),
        ),
        Operation(
            "ec2",
            "describe-security-groups",
            "DescribeSecurityGroups",
            False,
            True,
            (Param("--group-ids", "GroupIds", "group_id", many=True), Param("--filters", "Filters", "filters", many=True)),
        ),
        # fixture-internal (not reachable through `aws-lab aws`)
        Operation("s3", "head-bucket", "HeadBucket", False, False, (Param("--bucket", "Bucket", "bucket", required=True),)),
        Operation("s3", "get-bucket-location", "GetBucketLocation", False, False, (Param("--bucket", "Bucket", "bucket", required=True),)),
        Operation("s3", "get-bucket-tagging", "GetBucketTagging", False, False, (Param("--bucket", "Bucket", "bucket", required=True),)),
        Operation("s3", "put-bucket-tagging", "PutBucketTagging", True, False),
        Operation("ec2", "describe-availability-zones", "DescribeAvailabilityZones", False, False),
        Operation("ec2", "create-vpc", "CreateVpc", True, False),
        Operation("ec2", "create-subnet", "CreateSubnet", True, False),
        Operation("ec2", "create-security-group", "CreateSecurityGroup", True, False),
    ]
    return {(op.service, op.cli_name): op for op in ops}


OPERATIONS = _ops()


def public_operations() -> list[Operation]:
    return [op for op in OPERATIONS.values() if op.public]


def internal_operations() -> list[Operation]:
    return [op for op in OPERATIONS.values() if not op.public]


def _check_value(value: str, flag: str) -> None:
    if value.lower().startswith(FORBIDDEN_VALUE_PREFIXES):
        raise LabError("operation_not_allowed", f"{flag}: URLs and file references are not accepted", {"flag": flag})


def _validate(kind: str, value: str, flag: str, op: Operation):
    _check_value(value, flag)
    if kind == "bucket":
        if not BUCKET_RE.match(value):
            raise LabError("invalid_argument", f"{flag}: not a valid bucket name", {"flag": flag})
        return value
    if kind in ID_RES:
        if not ID_RES[kind].match(value):
            raise LabError("invalid_argument", f"{flag}: malformed resource id", {"flag": flag})
        return value
    if kind == "filters":
        return _parse_filter(value, flag, op)
    raise LabError("internal_error", "unknown parameter kind")


def _parse_filter(text: str, flag: str, op: Operation) -> dict:
    match = re.fullmatch(r"Name=([^,]+),Values=(.+)", text)
    if not match:
        raise LabError("invalid_argument", f"{flag}: expected Name=NAME,Values=V1[,V2...]", {"flag": flag})
    name, values_text = match.group(1), match.group(2)
    allowed = FILTER_NAMES.get(op.cli_name, set())
    if name.startswith("tag:"):
        if not TAG_KEY_RE.match(name[4:]):
            raise LabError("invalid_argument", f"{flag}: invalid tag key in filter name", {"flag": flag})
    elif name not in allowed:
        raise LabError("operation_not_allowed", f"{flag}: filter {name!r} is not allowlisted", {"allowed": sorted(allowed) + ["tag:KEY"]})
    values = values_text.split(",")
    if len(values) > MAX_FILTER_VALUES or not all(FILTER_VALUE_RE.match(v) for v in values):
        raise LabError("invalid_argument", f"{flag}: filter values must match [A-Za-z0-9._:/-]{{1,128}} (max 10)", {"flag": flag})
    return {"Name": name, "Values": values}


def parse_cli(tokens: list[str], *, allow_internal: bool = False) -> tuple[Operation, dict]:
    """Parse ``SERVICE OPERATION [ARGS...]`` into an operation and API params."""
    if len(tokens) < 2:
        raise LabError("usage_error", "expected SERVICE OPERATION [ARGS...]")
    service, name, rest = tokens[0], tokens[1], tokens[2:]
    op = OPERATIONS.get((service, name))
    if op is None or (not op.public and not allow_internal):
        raise LabError(
            "operation_not_allowed",
            f"{service} {name} is not an allowlisted operation",
            {"allowed": sorted(f"{o.service} {o.cli_name}" for o in public_operations())},
        )
    by_flag = {p.flag: p for p in op.params}
    params: dict = {}
    i = 0
    while i < len(rest):
        token = rest[i]
        if not token.startswith("--"):
            raise LabError("invalid_argument", f"unexpected positional argument {token[:40]!r}")
        flag, eq, inline = token.partition("=")
        if flag in FORBIDDEN_OPTIONS:
            raise LabError("operation_not_allowed", f"{flag} is controlled by the lab and cannot be overridden", {"flag": flag})
        param = by_flag.get(flag)
        if param is None:
            raise LabError(
                "operation_not_allowed", f"{flag} is not an accepted parameter of {service} {name}", {"accepted": sorted(by_flag)}
            )
        if param.api_name in params:
            raise LabError("invalid_argument", f"{flag} given more than once")
        values: list[str] = []
        if eq:
            values.append(inline)
            i += 1
        else:
            i += 1
            while i < len(rest) and not rest[i].startswith("--"):
                values.append(rest[i])
                i += 1
                if not param.many:
                    break
        if not values:
            raise LabError("invalid_argument", f"{flag} requires a value")
        limit = MAX_FILTERS if param.kind == "filters" else MAX_LIST
        if not param.many and len(values) != 1:
            raise LabError("invalid_argument", f"{flag} takes exactly one value")
        if len(values) > limit:
            raise LabError("invalid_argument", f"{flag} accepts at most {limit} values")
        parsed = [_validate(param.kind, v, flag, op) for v in values]
        params[param.api_name] = parsed if param.many else parsed[0]
    for param in op.params:
        if param.required and param.api_name not in params:
            raise LabError("invalid_argument", f"{param.flag} is required for {service} {name}")
    return op, params


def contract() -> dict:
    """Machine-readable description, compared with docs/contracts/aws-operations.json."""
    return {
        "schema_version": 1,
        "syntax": (
            "aws-lab aws NAME -- SERVICE OPERATION [--flag VALUE...]; "
            "list flags take several space-separated values; --flag=VALUE is also accepted"
        ),
        "public": [
            {
                "service": op.service,
                "operation": op.cli_name,
                "api": op.api_name,
                "mutating": op.mutating,
                "parameters": [
                    {"flag": p.flag, "api_name": p.api_name, "kind": p.kind, "required": p.required, "many": p.many} for p in op.params
                ],
                "filter_names": sorted(FILTER_NAMES.get(op.cli_name, set())) + (["tag:KEY"] if op.cli_name in FILTER_NAMES else []),
            }
            for op in sorted(public_operations(), key=lambda o: (o.service, o.cli_name))
        ],
        "fixture_internal": [
            {"service": op.service, "operation": op.cli_name, "api": op.api_name, "mutating": op.mutating}
            for op in sorted(internal_operations(), key=lambda o: (o.service, o.cli_name))
        ],
        "forbidden_options": sorted(FORBIDDEN_OPTIONS),
        "forbidden_value_prefixes": list(FORBIDDEN_VALUE_PREFIXES),
        "limits": {"max_list_values": MAX_LIST, "max_filters": MAX_FILTERS, "max_filter_values": MAX_FILTER_VALUES},
    }
