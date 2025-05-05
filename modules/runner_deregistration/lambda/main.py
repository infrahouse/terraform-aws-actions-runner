import logging
from os import environ

from infrahouse_core.aws.asg_instance import ASGInstance

from infrahouse_core.github import GitHubActions, get_tmp_token

from infrahouse_core.aws import get_secret

import boto3
from infrahouse_core.aws.asg import ASG

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)


def lambda_handler(event, context):
    LOG.info(f"{event = }")
    secretsmanager_client = boto3.client("secretsmanager")
    asg_name = environ["ASG_NAME"]
    asg = ASG(asg_name=asg_name)
    hook_name = environ["HOOK_NAME"]

    github_org_name = environ["GITHUB_ORG_NAME"]
    github_token = (
        get_secret(secretsmanager_client, environ["GITHUB_SECRET"])
        if environ["GITHUB_SECRET_TYPE"] == "token"
        else get_tmp_token(int(environ["GH_APP_ID"]), environ["GITHUB_SECRET"], github_org_name)
    )
    gha = GitHubActions(github_token, github_org_name)

    for instance in asg.instances:
        if instance.lifecycle_state == "Terminating:Wait":
            _deregister_instance_id(gha, instance, asg, hook_name)

    # Warm pool instances
    client = boto3.client("autoscaling")
    response = client.describe_warm_pool(AutoScalingGroupName=asg_name)
    for item in response["Instances"]:
        instance = ASGInstance(item["InstanceId"])
        if instance.lifecycle_state == "Warmed:Terminating:Wait":
            _deregister_instance_id(gha, instance, asg, hook_name)

        elif instance.lifecycle_state == "Warmed:Pending:Wait":
            """
            This is a situation when the instance comes back to the warm pool.
            """
            asg.complete_lifecycle_action(
                hook_name=hook_name,
                instance_id=instance.instance_id
            )
            LOG.info(f"{instance.instance_id = }: Lifecycle hook {hook_name = } is successfully complete.")


def _deregister_instance_id(gha: GitHubActions, instance: ASGInstance, asg: ASG, hook_name):
    runner = gha.find_runner_by_label(f"instance_id:{instance.instance_id}")
    if runner:
        if runner["status"] == "online" and runner["busy"]:
            LOG.info(f"Runner {runner['name']} is busy")
            return
        else:
            LOG.info(f"Runner {runner['name']} is offline or idle. Will deregister it.")
            """deregister here"""
            registration_token_secret_prefix = environ[
                "REGISTRATION_TOKEN_SECRET_PREFIX"
            ]
            registration_token_secret = (
                f"{registration_token_secret_prefix}-{instance.instance_id}"
            )
            gha.deregister_runner(
                instance, runner["id"]
            )
            secretsmanager_client = boto3.client("secretsmanager")

            secretsmanager_client.delete_secret(
                SecretId=registration_token_secret, ForceDeleteWithoutRecovery=True
            )
            LOG.info(
                f"Registration token secret {registration_token_secret} is successfully removed."
            )
            asg.complete_lifecycle_action(
                hook_name=hook_name,
                instance_id=instance.instance_id
            )
            LOG.info(f"{instance.instance_id = }: Lifecycle hook {hook_name = } is successfully complete.")
