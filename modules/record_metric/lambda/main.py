import logging
from collections import Counter
from os import environ
from time import monotonic
from typing import Optional

from infrahouse_core.timeout import timeout

from infrahouse_core.github import (
    GitHubActions,
    GitHubAppTokenSource,
    GitHubAuth,
    GitHubTokenSource,
    KMSSigner,
    PEMSecretSigner,
)

from infrahouse_core.aws import get_secret

import boto3

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)

# Module-scope boto3 session and clients: created once at cold start so the ~8 MB
# botocore endpoints.json parse runs during INIT (uncapped CPU) instead of
# inside the handler's SIGALRM-bounded timeout window.
_session = boto3.Session()
_secretsmanager = _session.client("secretsmanager")
_cloudwatch = _session.client("cloudwatch")

# The permissions of the installation token, when the function authenticates as a GitHub App.
# It only lists the organization's runners: GitHub's "Self-hosted runners" organization
# permission, read.
GITHUB_TOKEN_PERMISSIONS = {"organization_self_hosted_runners": "read"}


def _build_token_source(secret_type: str) -> Optional[GitHubTokenSource]:
    """
    Build the GitHub App token source for the credential type Terraform configured.

    :param secret_type: ``GITHUB_SECRET_TYPE``: ``token``, ``pem`` or ``kms``.
    :return: The token source, or ``None`` for a classic token, which is read from its secret instead.
    :raises ValueError: If the credential type is unknown.
    """
    if secret_type == "token":
        return None
    if secret_type == "kms":
        signer = KMSSigner(environ["GITHUB_APP_KMS_KEY_ARN"], session=_session)
    elif secret_type == "pem":
        signer = PEMSecretSigner(environ["GITHUB_SECRET"], session=_session)
    else:
        raise ValueError(
            f"Unknown GITHUB_SECRET_TYPE {secret_type!r}: expected token, pem or kms."
        )
    return GitHubAppTokenSource(int(environ["GH_APP_ID"]), signer)


# Built once per execution environment, so a warm function reuses the signer and its KMS client.
_token_source = _build_token_source(environ["GITHUB_SECRET_TYPE"])


def lambda_handler(event, context):
    """
    AWS Lambda function to record metrics for GitHub runners in an Auto Scaling Group (ASG).

    This function retrieves the status of GitHub runners associated with the instances in the ASG,
    counts the number of idle and busy runners, and sends these metrics to AWS CloudWatch.

    :param event: The event data passed to the Lambda function.
    :type event: dict
    :param context: The context object providing runtime information about the Lambda function.
    :type context: LambdaContext
    :return: None
    """
    LOG.info(f"{event = }")
    asg_name = environ["ASG_NAME"]
    github = GitHubAuth(
        _get_github_token(environ["GITHUB_ORG_NAME"]), environ["GITHUB_ORG_NAME"]
    )
    gha = GitHubActions(github)

    status_counts = Counter()
    for runner in gha.find_runners_by_label(
        f"installation_id:{environ['INSTALLATION_ID']}"
    ):
        if runner and runner.status == "online":
            status_counts["busy" if runner.busy else "idle"] += 1

    LOG.info(f"{status_counts['idle'] = }, {status_counts['busy'] = }")

    _cloudwatch.put_metric_data(
        Namespace="GitHubRunners",
        MetricData=[
            {
                "MetricName": "BusyRunners",
                "Dimensions": [
                    {"Name": "asg_name", "Value": asg_name},
                ],
                "Value": status_counts["busy"],
                "Unit": "Count",
            },
            {
                "MetricName": "IdleRunners",
                "Dimensions": [
                    {"Name": "asg_name", "Value": asg_name},
                ],
                "Value": status_counts["idle"],
                "Unit": "Count",
            },
        ],
    )


def _get_github_token(org: str) -> str:
    """
    Return a GitHub token for the organization, within five seconds.

    :param org: The GitHub organization.
    :return: The classic token from its secret, or an installation token of the GitHub App
        with ``GITHUB_TOKEN_PERMISSIONS``.
    :raises TimeoutError: If getting the token takes more than five seconds.
    """
    started = monotonic()
    with timeout(5):
        if _token_source is None:
            token = get_secret(_secretsmanager, environ["GITHUB_SECRET"])
        else:
            token = _token_source.installation_token(
                org, permissions=GITHUB_TOKEN_PERMISSIONS
            )
    # How much of the five seconds the token took. With a GitHub App, that is the App's
    # installation lookup and the token exchange, each request with its own signed JWT.
    LOG.info(
        "Got a %s GitHub token in %.3f s.",
        environ["GITHUB_SECRET_TYPE"],
        monotonic() - started,
    )
    return token
