from os import environ

import boto3
from requests import get, delete


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


def deregister_runner(
    org_name, github_token_secret, instance_id
):
    """Remove the instance from DNS."""
    print(f"Deregistering runner {instance_id} from {org_name}")
    github_token = get_secret(github_token_secret)
    # Get runner id
    runner_id = get_runner_id(org_name, github_token, instance_id)
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


def lambda_handler(event, context):
    print(f"{event = }")
    if "LifecycleTransition" in event["detail"]:
        try:
            lifecycle_transition = event["detail"]["LifecycleTransition"]
            print(f"{lifecycle_transition = }")

            if lifecycle_transition == "autoscaling:EC2_INSTANCE_TERMINATING":
                deregister_runner(
                    environ["GITHUB_ORG_NAME"],
                    environ["GITHUB_TOKEN_SECRET"],
                    event["detail"]["EC2InstanceId"],
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
