import json
from textwrap import dedent
from time import sleep

import boto3
import pytest
import logging

from os import path as osp

from github import GithubIntegration
from github.Consts import MAX_JWT_EXPIRY
from infrahouse_toolkit.logging import setup_logging
from infrahouse_toolkit.terraform import terraform_apply
from infrahouse_toolkit.timeout import timeout
from requests import get

# "303467602807" is our test account
TEST_ACCOUNT = "303467602807"
TEST_ROLE_ARN = "arn:aws:iam::303467602807:role/actions-runner-tester"
DEFAULT_PROGRESS_INTERVAL = 10
TRACE_TERRAFORM = False
UBUNTU_CODENAME = "jammy"

LOG = logging.getLogger(__name__)
REGION = "us-west-1"
TEST_ZONE = "ci-cd.infrahouse.com"
GITHUB_ORG_NAME = "infrahouse"
TERRAFORM_ROOT_DIR = "test_data"
GH_APP_ID = 1016363


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
        help=f"GitHub Token with Admin permissions.",
    )
    parser.addoption(
        "--github-app-pem-secret",
        action="store",
        help=f"Secret ARN with a GitHub App PEM key.",
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
def github_app_pem_secret_arn(request):
    return request.config.getoption("--github-app-pem-secret")


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


@pytest.fixture()
def secretsmanager_client(boto3_session):
    return boto3_session.client("secretsmanager", region_name=REGION)


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


def ensure_runners(token, timeout_time=900):
    try:
        with timeout(timeout_time):
            while True:
                try:
                    response = get(
                        f"https://api.github.com/orgs/{GITHUB_ORG_NAME}/actions/runners",
                        headers={
                            "Accept": "application/vnd.github+json",
                            "Authorization": f"Bearer {token}",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                        timeout=30,
                    )
                    response.raise_for_status()
                    runners = response.json()["runners"]
                    print(json.dumps(runners, indent=4))
                    assert len([r for r in runners if r["status"] == "online"]) > 0
                    break
                except AssertionError:
                    LOG.info("No registered runners yet")
                    sleep(3)
    except TimeoutError:
        LOG.error("No registered runners after %d seconds.", timeout_time)
        assert False


def get_secret(secretsmanager_client, secret_name):
    """
    Retrieve a value of a secret by its name.
    """
    return secretsmanager_client.get_secret_value(
        SecretId=secret_name,
    )["SecretString"]


def get_tmp_token(secretsmanager_client, pem_key_secret: str) -> str:
    """
    Generate a temporary GitHub token from GitHUb App PEM key

    :param secretsmanager_client: Boto3 client
    :param pem_key_secret: Secret ARN with the PEM key.
    :type pem_key_secret: str
    :return: GitHub token
    :rtype: str
    """
    github_client = GithubIntegration(
        GH_APP_ID,
        get_secret(secretsmanager_client, pem_key_secret),
        jwt_expiry=MAX_JWT_EXPIRY,
    )
    for installation in github_client.get_installations():
        if installation.target_type == "Organization":
            if GITHUB_ORG_NAME == _get_org_name(github_client, installation.id):
                return github_client.get_access_token(
                    installation_id=installation.id
                ).token

    raise RuntimeError(
        f"Could not find installation of {GH_APP_ID} in organization {GITHUB_ORG_NAME}"
    )


def _get_org_name(github_client, installation_id):
    url = f"https://api.github.com/app/installations/{installation_id}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_client.create_jwt()}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = get(url, headers=headers, timeout=600)
    return response.json()["account"]["login"]
