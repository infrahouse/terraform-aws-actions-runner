import logging
from os import environ

from github import GithubIntegration
from github.Consts import MAX_JWT_EXPIRY
from requests import get

from infrahouse_core.aws import get_secret

import boto3
from infrahouse_core.aws.asg import ASG

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)


def get_tmp_token(pem_key_secret: str, github_org_name: str) -> str:
    """
    Generate a temporary GitHub token from GitHUb App PEM key

    :param pem_key_secret: Secret ARN with the PEM key.
    :type pem_key_secret: str
    :param github_org_name: GitHub Organization. Used to find GitHub App installation.
    :return: GitHub token
    :rtype: str
    """
    secretsmanager_client = boto3.client("secretsmanager")
    gh_app_id = environ["GH_APP_ID"]
    github_client = GithubIntegration(
        gh_app_id,
        get_secret(secretsmanager_client, pem_key_secret),
        jwt_expiry=MAX_JWT_EXPIRY,
    )
    for installation in github_client.get_installations():
        if installation.target_type == "Organization":
            if github_org_name == _get_org_name(github_client, installation.id):
                return github_client.get_access_token(
                    installation_id=installation.id
                ).token

    raise RuntimeError(
        f"Could not find installation of {gh_app_id} in organization {github_org_name}"
    )


def get_github_runners(github_token, org):
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
    }

    response = get(
        f"https://api.github.com/orgs/{org}/actions/runners", headers=headers
    )
    response.raise_for_status()

    return response.json()["runners"]


def find_runner_by_label(runners, label):
    LOG.info(f"Looking for a runner with label {label}")
    for runner in runners:
        labels = [l["name"] for l in runner["labels"]]
        if label in labels:
            LOG.info(f"Found {runner['name']}!")
            return runner

    LOG.warning(f"Couldn't find a runner with label {label}")


def lambda_handler(event, context):
    LOG.info(f"{event = }")
    secretsmanager_client = boto3.client("secretsmanager")
    asg_name = environ["ASG_NAME"]
    asg = ASG(asg_name=asg_name)

    github_org_name = environ["GITHUB_ORG_NAME"]
    github_token = (
        get_secret(secretsmanager_client, environ["GITHUB_SECRET"])
        if environ["GITHUB_SECRET_TYPE"] == "token"
        else get_tmp_token(environ["GITHUB_SECRET"], github_org_name)
    )
    runners = get_github_runners(github_token, github_org_name)

    idle_count = 0
    busy_count = 0
    for instance in asg.instances:
        runner = find_runner_by_label(runners, f"instance_id:{instance.instance_id}")
        if runner:
            if runner["status"] == "online":
                if runner["busy"]:
                    LOG.info(f"Runner {runner['name']} is busy")
                    busy_count += 1
                else:
                    LOG.info(f"Runner {runner['name']} is idle")
                    idle_count += 1
            else:
                LOG.info(f"Runner {runner['name']} is offline")

    LOG.info(f"{idle_count = }")
    LOG.info(f"{busy_count = }")
    cloudwatch = boto3.client("cloudwatch")

    # Send the custom metric
    cloudwatch.put_metric_data(
        Namespace="GitHubRunners",
        MetricData=[
            {
                "MetricName": "IdleRunners",
                "Dimensions": [
                    {"Name": "asg_name", "Value": asg_name},
                ],
                "Value": idle_count,
                "Unit": "Count",
            }
        ],
    )
    cloudwatch.put_metric_data(
        Namespace="GitHubRunners",
        MetricData=[
            {
                "MetricName": "BusyRunners",
                "Dimensions": [
                    {"Name": "asg_name", "Value": asg_name},
                ],
                "Value": busy_count,
                "Unit": "Count",
            }
        ],
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
