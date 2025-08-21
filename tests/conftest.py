from time import sleep

import pytest
import logging


from github import GithubIntegration
from github.Consts import MAX_JWT_EXPIRY
from infrahouse_core.aws.asg import ASG
from infrahouse_core.aws.asg_instance import ASGInstance
from infrahouse_core.github import GitHubActions, GitHubAuth
from infrahouse_core.logging import setup_logging
from infrahouse_core.timeout import timeout
from pytest_infrahouse.plugin import aws_region
from requests import get


LOG = logging.getLogger()
GITHUB_ORG_NAME = "infrahouse"
TERRAFORM_ROOT_DIR = "test_data"
GH_APP_ID = 1016363


setup_logging(LOG, debug=True)


def pytest_addoption(parser):
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
def github_token(request):
    return request.config.getoption("--github-token")


@pytest.fixture(scope="session")
def github_app_pem_secret_arn(request):
    return request.config.getoption("--github-app-pem-secret")


def ensure_runners(gha: GitHubActions, aws_region, timeout_time=900, test_role_arn=None):
    try:
        with timeout(timeout_time):
            while True:
                try:
                    runners = gha.find_runners_by_label("awesome")
                    LOG.info("Found %d runners", len(runners))
                    assert len(runners) > 0

                    LOG.info(
                        "Using runner %s (%s) to seed autoscaling instance.",
                        runners[0].name,
                        runners[0].instance_id,
                    )
                    asg_instance = ASGInstance(
                        runners[0].instance_id,
                        role_arn=test_role_arn,
                        region=aws_region,
                    )
                    asg = ASG(
                        asg_instance.asg_name, role_arn=test_role_arn, region=aws_region
                    )
                    wait_for_instance_refreshes(asg, refresh_wait_timeout=timeout_time)

                    for instance in asg.instances:
                        LOG.info("Checking instance %s", instance.instance_id)
                        runner = gha.find_runner_by_label(
                            f"instance_id:{instance.instance_id}"
                        )
                        assert runner.status == "online"

                    break
                except AssertionError:
                    LOG.info("No registered runners yet")
                    sleep(3)
    except TimeoutError:
        LOG.error("No registered runners after %d seconds.", timeout_time)
        assert False


def wait_for_instance_refreshes(asg: ASG, refresh_wait_timeout=300):
    complete = {
        "Successful",
        "Failed",
        "Cancelled",
        "RollbackFailed",
        "RollbackSuccessful",
    }
    with timeout(refresh_wait_timeout):
        while True:
            refreshes = list(asg.instance_refreshes)  # snapshot current statuses
            if not refreshes:
                LOG.info("No instance refreshes found.")
                return

            # We are done only if *all* refreshes are in a complete state
            all_done = all(ir.get("Status") in complete for ir in refreshes)

            if all_done:
                return

            LOG.info("Waiting for instance refreshes to complete...")
            sleep(5)


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
