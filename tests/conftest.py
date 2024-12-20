import json
from time import sleep

import pytest
import logging


from github import GithubIntegration
from github.Consts import MAX_JWT_EXPIRY
from infrahouse_toolkit.logging import setup_logging
from infrahouse_toolkit.timeout import timeout
from requests import get

# "303467602807" is our test account
TEST_ACCOUNT = "303467602807"
# TEST_ROLE_ARN = "arn:aws:iam::303467602807:role/actions-runner-tester"
DEFAULT_PROGRESS_INTERVAL = 10
TRACE_TERRAFORM = False
UBUNTU_CODENAME = "jammy"

LOG = logging.getLogger(__name__)
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
