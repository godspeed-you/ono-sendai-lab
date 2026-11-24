"""Scenario manifests, seed and independent verification (LAB-042, LAB-060..062).

Manifests (``scenarios/*.json``) are synthetic input. Seed logic creates
resources through the managed client only; verification re-reads every
resource through AWS APIs and asserts identities, attributes, relationships
and counts of lab-owned resources. Expected values come from the manifest,
observed values only from API responses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import LabError

SCENARIO_ID_RE = re.compile(r"^aws-[a-z0-9-]{1,40}$")
TAG_SCENARIO = "ono-sendai-lab:scenario"
TAG_GENERATION = "ono-sendai-lab:generation"
TAG_LOGICAL = "ono-sendai-lab:logical-id"
SUPPORTED = ("aws-empty", "aws-minimal")


# --- manifests (host side) ---------------------------------------------


def load_manifest(scenarios_dir: Path, scenario_id: str) -> dict:
    if not SCENARIO_ID_RE.match(scenario_id or "") or scenario_id not in SUPPORTED:
        raise LabError("unknown_scenario", f"unknown scenario {scenario_id!r}", {"supported": list(SUPPORTED)})
    path = scenarios_dir / f"{scenario_id}.json"
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LabError("internal_error", "scenario manifest is unreadable", {"scenario": scenario_id}) from exc
    validate_manifest(manifest)
    return manifest


def list_manifests(scenarios_dir: Path) -> list[dict]:
    return [load_manifest(scenarios_dir, sid) for sid in SUPPORTED]


def validate_manifest(manifest) -> None:
    if not isinstance(manifest, dict):
        raise LabError("invalid_argument", "manifest must be an object")
    if manifest.get("schema_version") != 1 or manifest.get("id") not in SUPPORTED:
        raise LabError("invalid_argument", "manifest id/schema_version invalid")
    resources = manifest.get("resources")
    if not isinstance(resources, list):
        raise LabError("invalid_argument", "manifest resources must be a list")
    logical = set()
    for res in resources:
        if not isinstance(res, dict) or res.get("type") not in ("s3-bucket", "ec2-vpc", "ec2-subnet", "ec2-security-group"):
            raise LabError("invalid_argument", "manifest resource type unsupported")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(res.get("logical_id", ""))):
            raise LabError("invalid_argument", "manifest logical_id invalid")
        logical.add(res["logical_id"])
    for rel in manifest.get("relations", []):
        if rel.get("from") not in logical or rel.get("to") not in logical:
            raise LabError("invalid_argument", "manifest relation references unknown resource")


# --- in-sandbox helpers ---------------------------------------------------


def bucket_create_params(bucket: str, region: str) -> dict:
    """Explicit regional S3 adaptation: outside us-east-1 CreateBucket needs a
    LocationConstraint naming the session region (qualified on 1.0.598)."""
    params: dict = {"Bucket": bucket}
    if region != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}
    return params


def _tags(resource: dict, manifest: dict, labels: dict) -> list[dict]:
    tags = dict(resource.get("tags", {}))
    tags[TAG_SCENARIO] = manifest["id"]
    tags[TAG_GENERATION] = labels["generation"]
    tags[TAG_LOGICAL] = resource["logical_id"]
    return [{"Key": k, "Value": v} for k, v in sorted(tags.items())]


def _tag_dict(tag_list) -> dict:
    return {t.get("Key"): t.get("Value") for t in tag_list or []}


def bucket_name(resource: dict, labels: dict) -> str:
    return resource["name_template"].format(generation12=labels["generation"][:12])


def observe_baseline(client) -> dict:
    """Observed default state of a fresh generation (not assumed empty)."""
    ident = client.call("sts", "GetCallerIdentity", {}, mutating=False)
    buckets = client.call("s3", "ListBuckets", {}, mutating=False).get("Buckets", [])
    vpcs = client.call("ec2", "DescribeVpcs", {}, mutating=False).get("Vpcs", [])
    subnets = client.call("ec2", "DescribeSubnets", {}, mutating=False).get("Subnets", [])
    groups = client.call("ec2", "DescribeSecurityGroups", {}, mutating=False).get("SecurityGroups", [])
    return {
        "identity": {"account": ident.get("Account"), "arn": ident.get("Arn")},
        "s3_buckets": sorted(b.get("Name") for b in buckets),
        "ec2_vpcs": sorted(v.get("VpcId") for v in vpcs),
        "ec2_default_vpcs": sorted(v.get("VpcId") for v in vpcs if v.get("IsDefault")),
        "ec2_subnets": sorted(s.get("SubnetId") for s in subnets),
        "ec2_security_groups": sorted(g.get("GroupId") for g in groups),
        "lab_tagged": _lab_tagged_counts(client),
    }


def _lab_tagged_counts(client) -> dict:
    flt = [{"Name": "tag-key", "Values": [TAG_SCENARIO]}]
    return {
        "vpcs": len(client.call("ec2", "DescribeVpcs", {"Filters": flt}, mutating=False).get("Vpcs", [])),
        "subnets": len(client.call("ec2", "DescribeSubnets", {"Filters": flt}, mutating=False).get("Subnets", [])),
        "security_groups": len(client.call("ec2", "DescribeSecurityGroups", {"Filters": flt}, mutating=False).get("SecurityGroups", [])),
    }


def seed(client, manifest, labels: dict, fault: str | None = None) -> dict:
    """Create the manifest's resources in order. Returns partial evidence on failure."""
    validate_manifest(manifest)
    if not isinstance(labels.get("generation"), str):
        raise LabError("invalid_argument", "labels.generation required")
    mapping: dict[str, str] = {}
    steps: list[dict] = []
    for res in manifest["resources"]:
        lid = res["logical_id"]
        try:
            if res["type"] == "s3-bucket":
                name = bucket_name(res, labels)
                client.call("s3", "CreateBucket", bucket_create_params(name, client.region), mutating=True)
                mapping[lid] = name
                steps.append({"operation": "s3 create-bucket", "logical_id": lid})
                client.call(
                    "s3",
                    "PutBucketTagging",
                    {"Bucket": name, "Tagging": {"TagSet": _tags(res, manifest, labels)}},
                    mutating=True,
                )
                steps.append({"operation": "s3 put-bucket-tagging", "logical_id": lid})
            elif res["type"] == "ec2-vpc":
                out = client.call(
                    "ec2",
                    "CreateVpc",
                    {"CidrBlock": res["cidr"], "TagSpecifications": [{"ResourceType": "vpc", "Tags": _tags(res, manifest, labels)}]},
                    mutating=True,
                )
                mapping[lid] = out["Vpc"]["VpcId"]
                steps.append({"operation": "ec2 create-vpc", "logical_id": lid})
            elif res["type"] == "ec2-subnet":
                cidr = "10.99.1.0/24" if fault == "subnet-outside-vpc" else res["cidr"]
                out = client.call(
                    "ec2",
                    "CreateSubnet",
                    {
                        "VpcId": mapping[res["vpc"]],
                        "CidrBlock": cidr,
                        "TagSpecifications": [{"ResourceType": "subnet", "Tags": _tags(res, manifest, labels)}],
                    },
                    mutating=True,
                )
                mapping[lid] = out["Subnet"]["SubnetId"]
                steps.append({"operation": "ec2 create-subnet", "logical_id": lid})
            elif res["type"] == "ec2-security-group":
                out = client.call(
                    "ec2",
                    "CreateSecurityGroup",
                    {
                        "GroupName": res["group_name"],
                        "Description": res["description"],
                        "VpcId": mapping[res["vpc"]],
                        "TagSpecifications": [{"ResourceType": "security-group", "Tags": _tags(res, manifest, labels)}],
                    },
                    mutating=True,
                )
                mapping[lid] = out["GroupId"]
                steps.append({"operation": "ec2 create-security-group", "logical_id": lid})
        except LabError as exc:
            return {
                "ok": False,
                "mapping": mapping,
                "steps": steps,
                "failed": {"logical_id": lid, "type": res["type"], "error": exc.to_dict()},
            }
    return {"ok": True, "mapping": mapping, "steps": steps}


class _Assertions:
    def __init__(self):
        self.items: list[dict] = []

    def check(self, aid: str, expected, observed) -> bool:
        passed = expected == observed
        self.items.append({"id": aid, "expected": expected, "observed": observed, "passed": passed})
        return passed

    @property
    def passed(self) -> bool:
        return bool(self.items) and all(i["passed"] for i in self.items)


def verify(client, manifest, labels: dict, mapping: dict, baseline: dict) -> dict:
    validate_manifest(manifest)
    a = _Assertions()
    ident = client.call("sts", "GetCallerIdentity", {}, mutating=False)
    a.check("identity.account", baseline.get("identity", {}).get("account"), ident.get("Account"))
    buckets = sorted(b.get("Name") for b in client.call("s3", "ListBuckets", {}, mutating=False).get("Buckets", []))
    extra_buckets = sorted(set(buckets) - set(baseline.get("s3_buckets", [])))
    gen = labels["generation"]
    gen_filter = [{"Name": f"tag:{TAG_GENERATION}", "Values": [gen]}]
    observed: dict = {"identity": {"account": ident.get("Account")}, "s3_buckets_beyond_baseline": extra_buckets}
    if manifest["id"] == "aws-empty":
        a.check("s3.lab_buckets_beyond_baseline", [], extra_buckets)
        counts = _lab_tagged_counts(client)
        a.check("ec2.lab_tagged_resources", {"vpcs": 0, "subnets": 0, "security_groups": 0}, counts)
        vpcs = sorted(v.get("VpcId") for v in client.call("ec2", "DescribeVpcs", {}, mutating=False).get("Vpcs", []))
        a.check("ec2.vpcs_equal_baseline", baseline.get("ec2_vpcs"), vpcs)
        observed.update({"lab_tagged": counts, "ec2_vpcs": vpcs})
        return {"passed": a.passed, "assertions": a.items, "observed": observed}

    by_lid = {r["logical_id"]: r for r in manifest["resources"]}
    for lid in by_lid:
        a.check(f"{lid}.mapped", True, lid in mapping)
    if not all(lid in mapping for lid in by_lid):
        return {"passed": False, "assertions": a.items, "observed": observed}
    expected_buckets = sorted(mapping[r["logical_id"]] for r in manifest["resources"] if r["type"] == "s3-bucket")
    a.check("s3.lab_buckets_beyond_baseline", expected_buckets, extra_buckets)
    for res in manifest["resources"]:
        lid = res["logical_id"]
        actual = mapping[lid]
        expected_tags = _tag_dict(_tags(res, manifest, labels))
        if res["type"] == "s3-bucket":
            a.check(f"{lid}.name", bucket_name(res, labels), actual)
            head = client.call("s3", "HeadBucket", {"Bucket": actual}, mutating=False)
            loc = client.call("s3", "GetBucketLocation", {"Bucket": actual}, mutating=False)
            a.check(f"{lid}.region", client.region, loc.get("LocationConstraint"))
            a.check(f"{lid}.head_region", client.region, head.get("BucketRegion"))
            tags = _tag_dict(client.call("s3", "GetBucketTagging", {"Bucket": actual}, mutating=False).get("TagSet"))
            a.check(f"{lid}.tags", expected_tags, {k: tags.get(k) for k in expected_tags})
            observed[lid] = {"name": actual, "region": loc.get("LocationConstraint")}
        elif res["type"] == "ec2-vpc":
            vpcs = client.call("ec2", "DescribeVpcs", {"VpcIds": [actual]}, mutating=False).get("Vpcs", [])
            a.check(f"{lid}.exists", 1, len(vpcs))
            vpc = vpcs[0] if vpcs else {}
            a.check(f"{lid}.cidr", res["cidr"], vpc.get("CidrBlock"))
            a.check(f"{lid}.is_default", False, vpc.get("IsDefault"))
            a.check(f"{lid}.tags", expected_tags, {k: _tag_dict(vpc.get("Tags")).get(k) for k in expected_tags})
            observed[lid] = {"id": actual, "cidr": vpc.get("CidrBlock")}
        elif res["type"] == "ec2-subnet":
            subnets = client.call("ec2", "DescribeSubnets", {"SubnetIds": [actual]}, mutating=False).get("Subnets", [])
            a.check(f"{lid}.exists", 1, len(subnets))
            sub = subnets[0] if subnets else {}
            a.check(f"{lid}.cidr", res["cidr"], sub.get("CidrBlock"))
            a.check(f"{lid}.tags", expected_tags, {k: _tag_dict(sub.get("Tags")).get(k) for k in expected_tags})
            observed[lid] = {"id": actual, "cidr": sub.get("CidrBlock"), "vpc_id": sub.get("VpcId")}
        elif res["type"] == "ec2-security-group":
            groups = client.call("ec2", "DescribeSecurityGroups", {"GroupIds": [actual]}, mutating=False).get("SecurityGroups", [])
            a.check(f"{lid}.exists", 1, len(groups))
            grp = groups[0] if groups else {}
            a.check(f"{lid}.group_name", res["group_name"], grp.get("GroupName"))
            a.check(f"{lid}.tags", expected_tags, {k: _tag_dict(grp.get("Tags")).get(k) for k in expected_tags})
            observed[lid] = {"id": actual, "group_name": grp.get("GroupName"), "vpc_id": grp.get("VpcId")}
    for rel in manifest.get("relations", []):
        src, dst = rel["from"], rel["to"]
        a.check(f"relation.{src}->{dst}", mapping[dst], observed.get(src, {}).get("vpc_id"))
    # Counts of lab-owned resources for this generation (discovery by tags).
    counts = {
        "vpcs": len(client.call("ec2", "DescribeVpcs", {"Filters": gen_filter}, mutating=False).get("Vpcs", [])),
        "subnets": len(client.call("ec2", "DescribeSubnets", {"Filters": gen_filter}, mutating=False).get("Subnets", [])),
        "security_groups": len(
            client.call("ec2", "DescribeSecurityGroups", {"Filters": gen_filter}, mutating=False).get("SecurityGroups", [])
        ),
    }
    expected_counts = {
        "vpcs": sum(1 for r in manifest["resources"] if r["type"] == "ec2-vpc"),
        "subnets": sum(1 for r in manifest["resources"] if r["type"] == "ec2-subnet"),
        "security_groups": sum(1 for r in manifest["resources"] if r["type"] == "ec2-security-group"),
    }
    a.check("ec2.generation_owned_counts", expected_counts, counts)
    all_lab = _lab_tagged_counts(client)
    a.check("ec2.no_foreign_lab_resources", expected_counts, all_lab)
    observed["generation_owned_counts"] = counts
    return {"passed": a.passed, "assertions": a.items, "observed": observed}
