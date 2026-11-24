"""Seed/verify logic against a fake AWS client (external boundary fake)."""

import itertools

import pytest

from ono_sendai_lab import scenarios
from ono_sendai_lab.errors import LabError


class FakeAws:
    """Minimal in-memory stand-in for the managed client; behaves like the API."""

    region = "us-west-2"

    def __init__(self, fail_on=None, lie_about_vpc=False):
        self.buckets, self.vpcs, self.subnets, self.groups = {}, {}, {}, {}
        self.ids = itertools.count(1)
        self.fail_on = fail_on
        self.lie = lie_about_vpc
        self.calls = []

    def _tagged(self, items, filters):
        out = list(items.values())
        for f in filters or []:
            if f["Name"] == "tag-key":
                out = [i for i in out if any(t["Key"] in f["Values"] for t in i.get("Tags", []))]
            elif f["Name"].startswith("tag:"):
                key = f["Name"][4:]
                out = [i for i in out if any(t["Key"] == key and t["Value"] in f["Values"] for t in i.get("Tags", []))]
        return out

    def call(self, service, api, params, mutating):
        self.calls.append((service, api, mutating))
        if self.fail_on == api:
            raise LabError("aws_error", f"{api} failed", {"aws_code": "InvalidSubnet.Range"})
        if api == "GetCallerIdentity":
            return {"Account": "000000000000", "Arn": "arn:aws:iam::000000000000:user/emulator"}
        if api == "ListBuckets":
            return {"Buckets": [{"Name": n} for n in self.buckets]}
        if api == "CreateBucket":
            assert params["CreateBucketConfiguration"] == {"LocationConstraint": "us-west-2"}
            self.buckets[params["Bucket"]] = {"Tags": []}
            return {}
        if api == "PutBucketTagging":
            self.buckets[params["Bucket"]]["Tags"] = params["Tagging"]["TagSet"]
            return {}
        if api == "GetBucketTagging":
            return {"TagSet": self.buckets[params["Bucket"]]["Tags"]}
        if api == "HeadBucket":
            return {"BucketRegion": "us-west-2"}
        if api == "GetBucketLocation":
            return {"LocationConstraint": "us-west-2"}
        if api == "CreateVpc":
            vid = f"vpc-{next(self.ids):017x}"
            self.vpcs[vid] = {
                "VpcId": vid,
                "CidrBlock": params["CidrBlock"],
                "IsDefault": False,
                "Tags": params["TagSpecifications"][0]["Tags"],
            }
            return {"Vpc": self.vpcs[vid]}
        if api == "CreateSubnet":
            sid = f"subnet-{next(self.ids):017x}"
            vpc = "vpc-deadbeefdeadbeef0" if self.lie else params["VpcId"]
            self.subnets[sid] = {
                "SubnetId": sid,
                "VpcId": vpc,
                "CidrBlock": params["CidrBlock"],
                "Tags": params["TagSpecifications"][0]["Tags"],
            }
            return {"Subnet": self.subnets[sid]}
        if api == "CreateSecurityGroup":
            gid = f"sg-{next(self.ids):017x}"
            self.groups[gid] = {
                "GroupId": gid,
                "GroupName": params["GroupName"],
                "VpcId": params["VpcId"],
                "Tags": params["TagSpecifications"][0]["Tags"],
            }
            return {"GroupId": gid}
        if api == "DescribeVpcs":
            items = {k: v for k, v in self.vpcs.items() if not params.get("VpcIds") or k in params["VpcIds"]}
            return {"Vpcs": self._tagged(items, params.get("Filters"))}
        if api == "DescribeSubnets":
            items = {k: v for k, v in self.subnets.items() if not params.get("SubnetIds") or k in params["SubnetIds"]}
            return {"Subnets": self._tagged(items, params.get("Filters"))}
        if api == "DescribeSecurityGroups":
            items = {k: v for k, v in self.groups.items() if not params.get("GroupIds") or k in params["GroupIds"]}
            return {"SecurityGroups": self._tagged(items, params.get("Filters"))}
        raise AssertionError(api)


@pytest.fixture
def manifests(repo_root):
    return {sid: scenarios.load_manifest(repo_root / "scenarios", sid) for sid in scenarios.SUPPORTED}


LABELS = {"generation": "0123456789abcdef0123456789abcdef"}


def test_minimal_seed_and_verify(manifests):
    aws = FakeAws()
    baseline = scenarios.observe_baseline(aws)
    out = scenarios.seed(aws, manifests["aws-minimal"], LABELS)
    assert out["ok"] and set(out["mapping"]) == {"bucket", "vpc", "subnet", "security_group"}
    assert out["mapping"]["bucket"] == "osl-min-0123456789ab"
    check = scenarios.verify(aws, manifests["aws-minimal"], LABELS, out["mapping"], baseline)
    assert check["passed"], [a for a in check["assertions"] if not a["passed"]]
    ids = {a["id"] for a in check["assertions"]}
    assert {"relation.subnet->vpc", "relation.security_group->vpc", "ec2.generation_owned_counts"} <= ids


def test_verify_uses_observed_relations(manifests):
    aws = FakeAws(lie_about_vpc=True)
    baseline = scenarios.observe_baseline(aws)
    out = scenarios.seed(aws, manifests["aws-minimal"], LABELS)
    check = scenarios.verify(aws, manifests["aws-minimal"], LABELS, out["mapping"], baseline)
    assert not check["passed"]
    failed = [a["id"] for a in check["assertions"] if not a["passed"]]
    assert failed == ["relation.subnet->vpc"]


def test_partial_seed_reports_progress(manifests):
    aws = FakeAws(fail_on="CreateSubnet")
    out = scenarios.seed(aws, manifests["aws-minimal"], LABELS)
    assert not out["ok"]
    assert set(out["mapping"]) == {"bucket", "vpc"}
    assert out["failed"]["logical_id"] == "subnet"


def test_empty_scenario_creates_nothing(manifests):
    aws = FakeAws()
    baseline = scenarios.observe_baseline(aws)
    out = scenarios.seed(aws, manifests["aws-empty"], LABELS)
    assert out == {"ok": True, "mapping": {}, "steps": []}
    assert not any(mut for _, _, mut in aws.calls)
    assert scenarios.verify(aws, manifests["aws-empty"], LABELS, {}, baseline)["passed"]
    aws.buckets["stray"] = {"Tags": []}
    assert not scenarios.verify(aws, manifests["aws-empty"], LABELS, {}, baseline)["passed"]


def test_unknown_scenarios_rejected(repo_root):
    for sid in ("aws-degraded", "aws-enterprise", "../etc", ""):
        with pytest.raises(LabError) as exc:
            scenarios.load_manifest(repo_root / "scenarios", sid)
        assert exc.value.code == "unknown_scenario"


def test_bucket_params_are_explicitly_regional():
    assert scenarios.bucket_create_params("b", "us-west-2") == {
        "Bucket": "b",
        "CreateBucketConfiguration": {"LocationConstraint": "us-west-2"},
    }
    assert scenarios.bucket_create_params("b", "us-east-1") == {"Bucket": "b"}
