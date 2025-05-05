import logging
from os import environ

from infrahouse_core.github import get_tmp_token, GitHubActions

from botocore.exceptions import ClientError, BotoCoreError
from infrahouse_core.aws import get_secret
from infrahouse_core.aws.asg import ASG
from infrahouse_core.aws.asg_instance import ASGInstance
import boto3
from github import GithubException

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)


def lambda_handler(event, context):
    LOG.info(f"{event = }")
    hook_name = event["detail"]["LifecycleHookName"]
    LOG.info(f"{hook_name = }")
    github_org_name = environ["GITHUB_ORG_NAME"]
    asg_instance = ASGInstance(instance_id=event["detail"]["EC2InstanceId"])
    asg = ASG(asg_name=asg_instance.asg_name)

    if hook_name == "registration":
        try:
            registration_token_secret_prefix = environ[
                "REGISTRATION_TOKEN_SECRET_PREFIX"
            ]
            registration_token_secret = (
                f"{registration_token_secret_prefix}-{asg_instance.instance_id}"
            )

            gha = GitHubActions(
                _get_github_token(github_org_name), environ["GITHUB_ORG_NAME"]
            )
            gha.ensure_registration_token(registration_token_secret)
            asg.complete_lifecycle_action(
                hook_name=hook_name, instance_id=asg_instance.instance_id
            )
            LOG.info(f"Lifecycle hook {hook_name = } is successfully complete.")

        except (
            ClientError,
            BotoCoreError,
            GithubException,
            RuntimeError,
            TimeoutError,
        ) as err:
            LOG.error(err)
            asg.complete_lifecycle_action(
                hook_name=hook_name,
                result="ABANDON",
                instance_id=asg_instance.instance_id,
            )
            LOG.info(f"Lifecycle hook {hook_name = } is complete with ABANDON result.")

    elif hook_name == "bootstrap":
        """
        This hook is received either at instance start or when the instance
        comes back from hibernation.
        When the instance boots the first time, cloud-init competes the lifecycle
        action after a successful puppet run
        When the instance comes back from the hibernation, it's already registered,
        thus known for the GitHubActions() class.
        """
        gha = GitHubActions(_get_github_token(github_org_name), github_org_name)
        runner = gha.find_runner_by_label(f"instance_id:{asg_instance.instance_id}")
        if runner:
            asg.complete_lifecycle_action(
                hook_name=hook_name, instance_id=asg_instance.instance_id
            )
            LOG.info(f"Lifecycle hook {hook_name = } is successfully complete.")

    else:
        LOG.info(f"Ignoring hook {hook_name}")


def _get_github_token(org):
    return (
        get_secret(boto3.client("secretsmanager"), environ["GITHUB_SECRET"])
        if environ["GITHUB_SECRET_TYPE"] == "token"
        else get_tmp_token(int(environ["GH_APP_ID"]), environ["GITHUB_SECRET"], org)
    )
