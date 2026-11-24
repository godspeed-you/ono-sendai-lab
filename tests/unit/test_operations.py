import pytest

from ono_sendai_lab.errors import LabError
from ono_sendai_lab.operations import FORBIDDEN_OPTIONS, OPERATIONS, parse_cli, public_operations


def test_minimum_public_surface_is_exact():
    surface = sorted(f"{o.service} {o.cli_name}" for o in public_operations())
    assert surface == [
        "ec2 describe-security-groups",
        "ec2 describe-subnets",
        "ec2 describe-vpcs",
        "s3 create-bucket",
        "s3 delete-bucket",
        "s3 list-buckets",
        "sts get-caller-identity",
    ]


def test_parse_valid_forms():
    op, params = parse_cli(["s3", "create-bucket", "--bucket", "my-bucket-1"])
    assert op.mutating and params == {"Bucket": "my-bucket-1"}
    op, params = parse_cli(["s3", "delete-bucket", "--bucket=my-bucket-1"])
    assert params == {"Bucket": "my-bucket-1"}
    op, params = parse_cli(["ec2", "describe-vpcs", "--vpc-ids", "vpc-000000000000000a7", "vpc-0123456789abcdef0"])
    assert not op.mutating and params["VpcIds"] == ["vpc-000000000000000a7", "vpc-0123456789abcdef0"]
    _, params = parse_cli(["ec2", "describe-subnets", "--filters", "Name=vpc-id,Values=vpc-000000000000000a7", "Name=tag:Name,Values=a,b"])
    assert params["Filters"] == [
        {"Name": "vpc-id", "Values": ["vpc-000000000000000a7"]},
        {"Name": "tag:Name", "Values": ["a", "b"]},
    ]
    op, params = parse_cli(["sts", "get-caller-identity"])
    assert params == {}


@pytest.mark.parametrize("flag", sorted(FORBIDDEN_OPTIONS))
def test_lab_controlled_options_rejected(flag):
    with pytest.raises(LabError) as exc:
        parse_cli(["s3", "list-buckets", flag, "x"])
    assert exc.value.code == "operation_not_allowed" and exc.value.exit_code == 2


@pytest.mark.parametrize(
    "tokens,code",
    [
        (["iam", "create-user"], "operation_not_allowed"),
        (["s3", "put-object"], "operation_not_allowed"),
        (["ec2", "create-vpc"], "operation_not_allowed"),  # fixture-internal only
        (["s3", "head-bucket", "--bucket", "abc"], "operation_not_allowed"),
        (["s3", "create-bucket"], "invalid_argument"),
        (["s3", "create-bucket", "--bucket", "UPPER"], "invalid_argument"),
        (["s3", "create-bucket", "--bucket", "a;rm -rf"], "invalid_argument"),
        (["s3", "create-bucket", "--bucket", "file://x"], "operation_not_allowed"),
        (["s3", "create-bucket", "--bucket", "https://evil"], "operation_not_allowed"),
        (["s3", "create-bucket", "--bucket", "a", "b"], "invalid_argument"),
        (["s3", "create-bucket", "--bucket", "abc", "--bucket", "abd"], "invalid_argument"),
        (["s3", "create-bucket", "positional"], "invalid_argument"),
        (["ec2", "describe-vpcs", "--vpc-ids", "vpc-../../etc"], "invalid_argument"),
        (["ec2", "describe-vpcs", "--filters", "Name=owner-id,Values=1"], "operation_not_allowed"),
        (["ec2", "describe-vpcs", "--filters", "Name=vpc-id,Values=$(id)"], "invalid_argument"),
        (["ec2", "describe-vpcs", "--filters", "garbage"], "invalid_argument"),
        (["ec2", "describe-vpcs", "--vpc-ids"], "invalid_argument"),
        (["s3"], "usage_error"),
    ],
)
def test_rejections(tokens, code):
    with pytest.raises(LabError) as exc:
        parse_cli(tokens)
    assert exc.value.code == code


def test_list_limits():
    ids = [f"vpc-{i:017x}" for i in range(21)]
    with pytest.raises(LabError):
        parse_cli(["ec2", "describe-vpcs", "--vpc-ids", *ids])


def test_internal_operations_only_with_flag():
    op, _ = parse_cli(["s3", "head-bucket", "--bucket", "abc"], allow_internal=True)
    assert not op.public
    assert all(not op.public for key, op in OPERATIONS.items() if key[1].startswith("create-") and key[0] == "ec2")
