import signal
from contextlib import contextmanager
from os import environ
from time import sleep

import boto3
from requests import get, delete, post


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


def complete_lifecycle_action(
    lifecyclehookname,
    autoscalinggroupname,
    lifecycleactiontoken,
    instanceid,
    lifecycleactionresult="CONTINUE",
):
    print("Completing lifecycle hook action")
    print(f"{lifecyclehookname=}")
    print(f"{autoscalinggroupname=}")
    print(f"{lifecycleactiontoken=}")
    print(f"{lifecycleactionresult=}")
    print(f"{instanceid=}")
    client = boto3.client("autoscaling")
    client.complete_lifecycle_action(
        LifecycleHookName=lifecyclehookname,
        AutoScalingGroupName=autoscalinggroupname,
        LifecycleActionToken=lifecycleactiontoken,
        LifecycleActionResult=lifecycleactionresult,
        InstanceId=instanceid,
    )


def get_secret(secret_name):
    """
    Retrieve a value of a secret by its name.
    """
    client = boto3.client("secretsmanager")
    return client.get_secret_value(
        SecretId=secret_name,
    )["SecretString"]


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
    print(f"Couldn't find runner {instance_id}")


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


def record_lifecycle_heartbeat(
    lifecyclehookname,
    autoscalinggroupname,
    lifecycleactiontoken,
    instanceid,
):
    client = boto3.client("autoscaling")
    client.record_lifecycle_action_heartbeat(
        LifecycleHookName=lifecyclehookname,
        AutoScalingGroupName=autoscalinggroupname,
        LifecycleActionToken=lifecycleactiontoken,
        InstanceId=instanceid,
    )
    print(f"Updated lifecycle heartbeat for instance {instanceid}")


def wait_until_not_busy(
    org_name,
    github_token,
    runner_id,
    lifecyclehookname,
    autoscalinggroupname,
    lifecycleactiontoken,
    instanceid,
):
    """Wait until the runner is done with its active job."""

    with timeout(environ["LAMBDA_TIMEOUT"]):
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
                record_lifecycle_heartbeat(
                    lifecyclehookname,
                    autoscalinggroupname,
                    lifecycleactiontoken,
                    instanceid,
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
    org_name, github_token_secret, instance_id, registration_token_secret, runner_id
):
    """Remove the instance from DNS."""
    print(f"De-registering runner {instance_id} from {org_name}")
    github_token = get_secret(github_token_secret)
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
    print(f"Runner {runner_id}:{instance_id} is successfully deregistered.")
    # delete the registration token secret
    secretsmanager_client = boto3.client("secretsmanager")
    secretsmanager_client.delete_secret(
        SecretId=registration_token_secret, ForceDeleteWithoutRecovery=True
    )
    print(
        f"Registration token secret {registration_token_secret} is successfully removed."
    )


def get_instance_asg(instance_id) -> str:
    """Get instance's autoscaling group. If not a member, return None"""
    ec2_client = boto3.client("ec2")
    response = ec2_client.describe_instances(
        InstanceIds=[
            instance_id,
        ],
    )
    print(f"describe_instances({instance_id}): {response=}")
    for tag in response["Reservations"][0]["Instances"][0]["Tags"]:
        if tag["Key"] == "aws:autoscaling:groupName":
            return tag["Value"]


def get_instance_hostname(instance_id) -> str:
    """Get instance's hostname. Usually, something like ip-10-1-0-104."""
    ec2_client = boto3.client("ec2")
    response = ec2_client.describe_instances(
        InstanceIds=[
            instance_id,
        ],
    )
    print(f"describe_instances({instance_id}): {response=}")
    return response["Reservations"][0]["Instances"][0]["PrivateDnsName"].split(".")[0]


def register_runner(
    gh_org_name,
    gh_token_secret,
    autoscalinggroupname,
    instance_id,
    registration_token_secret,
):
    token = get_registration_token(gh_org_name, get_secret(gh_token_secret))
    # save the registration token in a secret
    secretsmanager_client = boto3.client("secretsmanager")
    secretsmanager_client.create_secret(
        Name=registration_token_secret,
        Description=f"GitHub Actions runner registration token for ASG {autoscalinggroupname}:{instance_id}",
        SecretString=token,
    )


def lambda_handler(event, context):
    print(f"{event = }")
    if "LifecycleTransition" in event["detail"]:
        try:
            lifecycle_transition = event["detail"]["LifecycleTransition"]
            print(f"{lifecycle_transition = }")
            asg_name = event["detail"]["AutoScalingGroupName"]
            instance_id = event["detail"]["EC2InstanceId"]
            registration_token_secret_prefix = environ[
                "REGISTRATION_TOKEN_SECRET_PREFIX"
            ]
            registration_token_secret = (
                f"{registration_token_secret_prefix}-{instance_id}"
            )

            if lifecycle_transition == "autoscaling:EC2_INSTANCE_TERMINATING":
                github_token = get_secret(environ["GITHUB_TOKEN_SECRET"])
                runner_id = get_runner_id(
                    environ["GITHUB_ORG_NAME"], github_token, instance_id
                )
                remove_all_labels(environ["GITHUB_ORG_NAME"], github_token, runner_id)
                wait_until_not_busy(
                    environ["GITHUB_ORG_NAME"],
                    github_token,
                    runner_id,
                    lifecyclehookname=event["detail"]["LifecycleHookName"],
                    autoscalinggroupname=event["detail"]["AutoScalingGroupName"],
                    lifecycleactiontoken=event["detail"]["LifecycleActionToken"],
                    instanceid=event["detail"]["EC2InstanceId"],
                )
                deregister_runner(
                    environ["GITHUB_ORG_NAME"],
                    environ["GITHUB_TOKEN_SECRET"],
                    instance_id,
                    registration_token_secret,
                    runner_id,
                )
            if lifecycle_transition == "autoscaling:EC2_INSTANCE_LAUNCHING":
                register_runner(
                    gh_org_name=environ["GITHUB_ORG_NAME"],
                    gh_token_secret=environ["GITHUB_TOKEN_SECRET"],
                    autoscalinggroupname=asg_name,
                    instance_id=instance_id,
                    registration_token_secret=registration_token_secret,
                )

        finally:
            print(
                f"Completing lifecycle hook {event['detail']['LifecycleHookName']} "
                f"on instance {event['detail']['EC2InstanceId']}"
            )
            complete_lifecycle_action(
                lifecyclehookname=event["detail"]["LifecycleHookName"],
                autoscalinggroupname=event["detail"]["AutoScalingGroupName"],
                lifecycleactiontoken=event["detail"]["LifecycleActionToken"],
                instanceid=event["detail"]["EC2InstanceId"],
                lifecycleactionresult="CONTINUE",
            )
