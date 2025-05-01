import signal
from contextlib import contextmanager
from os import environ
from time import sleep

from botocore.exceptions import ClientError, BotoCoreError
from infrahouse_core.aws import get_secret
from infrahouse_core.aws.asg import ASG
from infrahouse_core.aws.asg_instance import ASGInstance

import boto3
from github import GithubIntegration, GithubException
from github.Consts import MAX_JWT_EXPIRY
from requests import delete, get, post


@contextmanager
def timeout(seconds: int):
    """
    Timeout context manager.

    :param seconds: Max execution time in seconds.
    :type seconds: int
    :raise TimeoutError: when the code under a ``with`` is running
        more than ``seconds``.
    """

    def handler(signum, frame):
        if signum or frame:
            pass
        raise TimeoutError(f"Executing timed out after {seconds} seconds")

    original_handler = signal.signal(signal.SIGALRM, handler)
    try:
        signal.alarm(seconds)
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, original_handler)


def get_runner_id(org_name, github_token, instance_id):
    response = get(
        f"https://api.github.com/orgs/{org_name}/actions/runners",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    response.raise_for_status()
    runners = response.json()["runners"]
    for runner in runners:
        for label in runner["labels"]:
            if label["name"] == f"instance_id:{instance_id}":
                runner_id = runner["id"]
                print(f"Runner {instance_id} has id {runner_id}")
                return runner_id
    raise RuntimeError(f"Couldn't find runner {instance_id}")


def get_registration_token(org_name, github_token):
    response = post(
        f"https://api.github.com/orgs/{org_name}/actions/runners/registration-token",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["token"]


def wait_until_not_busy(
    org_name,
    github_token,
    runner_id,
    lifecyclehookname,
    asg_instance: ASGInstance,
):
    """Wait until the runner is done with its active job."""

    asg = ASG(asg_name=asg_instance.asg_name)

    with timeout(int(environ["LAMBDA_TIMEOUT"])):
        while True:
            response = get(
                f"https://api.github.com/orgs/{org_name}/actions/runners/{runner_id}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {github_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30,
            )
            response.raise_for_status()
            if response.json()["busy"]:
                print(f"Runner {runner_id} is still busy")
                sleep(1)
                asg.record_lifecycle_action_heartbeat(
                    hook_name=lifecyclehookname, instance_id=asg_instance.instance_id
                )
            else:
                print(f"Runner {runner_id} is not busy")
                return


def remove_all_labels(org_name, github_token, runner_id):
    """Remove all labels from a runner so GitHub doesn't schedule new jobs"""
    response = delete(
        f"https://api.github.com/orgs/{org_name}/actions/runners/{runner_id}/labels",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    response.raise_for_status()
    print(f"Removed all labels from runner {runner_id}.")


def deregister_runner(
    org_name,
    github_token,
    asg_instance: ASGInstance,
    registration_token_secret,
    runner_id,
):
    """Remove the instance from DNS."""
    print(f"De-registering runner {asg_instance.instance_id} from {org_name}")
    response = delete(
        f"https://api.github.com/orgs/{org_name}/actions/runners/{runner_id}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )
    response.raise_for_status()
    print(
        f"Runner {runner_id}:{asg_instance.instance_id} is successfully deregistered."
    )
    # delete the registration token secret
    secretsmanager_client = boto3.client("secretsmanager")
    secretsmanager_client.delete_secret(
        SecretId=registration_token_secret, ForceDeleteWithoutRecovery=True
    )
    print(
        f"Registration token secret {registration_token_secret} is successfully removed."
    )


def register_runner(
    gh_org_name, github_token, registration_token_secret, asg_instance: ASGInstance
):
    token = get_registration_token(gh_org_name, github_token)
    # save the registration token in a secret
    secretsmanager_client = boto3.client("secretsmanager")
    secretsmanager_client.create_secret(
        Name=registration_token_secret,
        Description=f"GitHub Actions runner registration token for ASG {asg_instance.asg_name}:{asg_instance.instance_id}",
        SecretString=token,
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


def lambda_handler(event, context):
    print(f"{event = }")
    if "LifecycleTransition" in event["detail"]:
        asg_instance = ASGInstance(instance_id=event["detail"]["EC2InstanceId"])
        asg = ASG(asg_name=asg_instance.asg_name)
        hook_name = event["detail"]["LifecycleHookName"]
        secretsmanager_client = boto3.client("secretsmanager")

        try:
            lifecycle_transition = event["detail"]["LifecycleTransition"]
            print(f"{lifecycle_transition = }")
            registration_token_secret_prefix = environ[
                "REGISTRATION_TOKEN_SECRET_PREFIX"
            ]
            registration_token_secret = (
                f"{registration_token_secret_prefix}-{asg_instance.instance_id}"
            )
            github_org_name = environ["GITHUB_ORG_NAME"]

            if lifecycle_transition == "autoscaling:EC2_INSTANCE_TERMINATING":
                github_token = (
                    get_secret(secretsmanager_client, environ["GITHUB_SECRET"])
                    if environ["GITHUB_SECRET_TYPE"] == "token"
                    else get_tmp_token(environ["GITHUB_SECRET"], github_org_name)
                )
                runner_id = get_runner_id(
                    github_org_name, github_token, asg_instance.instance_id
                )
                remove_all_labels(github_org_name, github_token, runner_id)
                wait_until_not_busy(
                    github_org_name,
                    github_token,
                    runner_id,
                    lifecyclehookname=hook_name,
                    asg_instance=asg_instance,
                )
                deregister_runner(
                    github_org_name,
                    github_token,
                    asg_instance,
                    registration_token_secret,
                    runner_id,
                )
                asg.complete_lifecycle_action(
                    hook_name=hook_name, instance_id=asg_instance.instance_id
                )
            if lifecycle_transition == "autoscaling:EC2_INSTANCE_LAUNCHING":
                github_token = (
                    get_secret(secretsmanager_client, environ["GITHUB_SECRET"])
                    if environ["GITHUB_SECRET_TYPE"] == "token"
                    else get_tmp_token(environ["GITHUB_SECRET"], github_org_name)
                )
                register_runner(
                    gh_org_name=github_org_name,
                    github_token=github_token,
                    registration_token_secret=registration_token_secret,
                    asg_instance=asg_instance,
                )
                asg.complete_lifecycle_action(
                    hook_name=hook_name, instance_id=asg_instance.instance_id
                )
        except (
            ClientError,
            BotoCoreError,
            GithubException,
            RuntimeError,
            TimeoutError,
        ) as err:
            print(err)
            asg.complete_lifecycle_action(
                hook_name=hook_name,
                result="ABANDON",
                instance_id=asg_instance.instance_id,
            )
