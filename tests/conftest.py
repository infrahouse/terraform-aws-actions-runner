import json
from textwrap import dedent

import boto3
import pytest
import logging

from os import path as osp

from infrahouse_toolkit.logging import setup_logging
from infrahouse_toolkit.terraform import terraform_apply

# "303467602807" is our test account
TEST_ACCOUNT = "303467602807"
TEST_ROLE_ARN = "arn:aws:iam::303467602807:role/actions-runner-tester"
DEFAULT_PROGRESS_INTERVAL = 10
TRACE_TERRAFORM = False
UBUNTU_CODENAME = "jammy"

LOG = logging.getLogger(__name__)
REGION = "us-east-2"
TEST_ZONE = "ci-cd.infrahouse.com"
TERRAFORM_ROOT_DIR = "test_data"

setup_logging(LOG, debug=True)


def pytest_addoption(parser):
    parser.addoption(
        "--keep-after",
        action="store_true",
        default=False,
        help="If specified, don't destroy resources.",
    )
    parser.addoption(
        "--test-role-arn",
        action="store",
        default=TEST_ROLE_ARN,
        help=f"AWS IAM role ARN that will create resources. Default, {TEST_ROLE_ARN}",
    )
    parser.addoption(
        "--github-token",
        action="store",
        required=True,
        help=f"GitHub Token with Admin permissions.",
    )


@pytest.fixture(scope="session")
def keep_after(request):
    return request.config.getoption("--keep-after")


@pytest.fixture(scope="session")
def test_role_arn(request):
    return request.config.getoption("--test-role-arn")


@pytest.fixture(scope="session")
def github_token(request):
    return request.config.getoption("--github-token")


@pytest.fixture(scope="session")
def aws_iam_role(test_role_arn):
    sts = boto3.client("sts")
    return sts.assume_role(
        RoleArn=test_role_arn, RoleSessionName=test_role_arn.split("/")[1]
    )


@pytest.fixture(scope="session")
def boto3_session(aws_iam_role):
    return boto3.Session(
        aws_access_key_id=aws_iam_role["Credentials"]["AccessKeyId"],
        aws_secret_access_key=aws_iam_role["Credentials"]["SecretAccessKey"],
        aws_session_token=aws_iam_role["Credentials"]["SessionToken"],
    )


@pytest.fixture(scope="session")
def ec2_client(boto3_session):
    assert boto3_session.client("sts").get_caller_identity()["Account"] == TEST_ACCOUNT
    return boto3_session.client("ec2", region_name=REGION)


@pytest.fixture(scope="session")
def ec2_client_map(ec2_client, boto3_session):
    regions = [reg["RegionName"] for reg in ec2_client.describe_regions()["Regions"]]
    ec2_map = {reg: boto3_session.client("ec2", region_name=reg) for reg in regions}

    return ec2_map


@pytest.fixture()
def route53_client(boto3_session):
    return boto3_session.client("route53", region_name=REGION)


@pytest.fixture()
def elbv2_client(boto3_session):
    return boto3_session.client("elbv2", region_name=REGION)


@pytest.fixture()
def autoscaling_client(boto3_session):
    assert boto3_session.client("sts").get_caller_identity()["Account"] == TEST_ACCOUNT
    return boto3_session.client("autoscaling", region_name=REGION)


@pytest.fixture(scope="session")
def service_network(boto3_session, test_role_arn, keep_after):
    terraform_module_dir = osp.join(TERRAFORM_ROOT_DIR, "service-network")
    # Create service network
    with open(osp.join(terraform_module_dir, "terraform.tfvars"), "w") as fp:
        fp.write(
            dedent(
                f"""
                role_arn = "{test_role_arn}"
                region   = "{REGION}"
                """
            )
        )
    with terraform_apply(
        terraform_module_dir,
        destroy_after=not keep_after,
        json_output=True,
        enable_trace=TRACE_TERRAFORM,
    ) as tf_service_network_output:
        yield tf_service_network_output


@pytest.fixture(scope="session")
def jumphost(boto3_session, service_network, test_role_arn, keep_after):
    subnet_public_ids = service_network["subnet_public_ids"]["value"]
    subnet_private_ids = service_network["subnet_private_ids"]["value"]

    terraform_module_dir = osp.join(TERRAFORM_ROOT_DIR, "jumphost")

    with open(osp.join(terraform_module_dir, "terraform.tfvars"), "w") as fp:
        fp.write(
            dedent(
                f"""
                role_arn  = "{test_role_arn}"
                region    = "{REGION}"
                test_zone = "{TEST_ZONE}"

                subnet_public_ids  = {json.dumps(subnet_public_ids)}
                subnet_private_ids = {json.dumps(subnet_private_ids)}
                """
            )
        )
    with terraform_apply(
        terraform_module_dir,
        destroy_after=not keep_after,
        json_output=True,
        enable_trace=TRACE_TERRAFORM,
    ) as tf_output:
        yield tf_output
