import logging
from os import environ

from infrahouse_core.timeout import timeout

from infrahouse_core.github import get_tmp_token, GitHubActions, GitHubAuth

from botocore.exceptions import ClientError, BotoCoreError
from infrahouse_core.aws import get_secret
from infrahouse_core.aws.asg import ASG
from infrahouse_core.aws.asg_instance import ASGInstance
import boto3
from github import GithubException

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)

HOOK_REGISTRATION = "registration"
HOOK_BOOTSTRAP = "bootstrap"


def lambda_handler(event, context):
    """
    AWS Lambda function to handle EC2 instance lifecycle events.

    This function processes events related to Auto Scaling Group (ASG) instance lifecycle hooks,
    specifically for registration. It retrieves the instance ID and hook name from the event,
    manages GitHub registration tokens, and completes the lifecycle action for the ASG.

    :param event: The event data containing details about the lifecycle hook and instance.
    :type event: dict
    :param context: The context object providing runtime information about the Lambda function.
    :type context: LambdaContext

    :return: None
    """
    LOG.info(f"{event = }")
    hook_name = event["detail"]["LifecycleHookName"]
    LOG.info(f"{hook_name = }")
    asg_instance = ASGInstance(instance_id=event["detail"]["EC2InstanceId"])
    github = GitHubAuth(
        _get_github_token(environ["GITHUB_ORG_NAME"]), environ["GITHUB_ORG_NAME"]
    )
    gha = GitHubActions(github)

    if hook_name == HOOK_REGISTRATION:
        """
        This hook is received either at instance start or when the instance or the
        instance comes back from hibernation.

        The goal of this lifecycle action is to ensure a registration token
        is obtained and stored in a secret.
        """
        _handle_registration_hook(asg_instance, hook_name, gha)

    elif hook_name == HOOK_BOOTSTRAP:
        """
        This hook is received either at instance start or when the instance
        comes back from hibernation.
        When the instance boots the first time, cloud-init competes the lifecycle
        action after a successful puppet run
        When the instance comes back from the hibernation, it's already registered,
        thus known for the GitHubActions() class.
        """
        wait_timeout = int(environ["LAMBDA_TIMEOUT"])
        _handle_bootstrap_hook(asg_instance, hook_name, gha, wait_timeout=wait_timeout)

    else:
        LOG.info(f"Ignoring hook {hook_name}")


def _handle_registration_hook(
    asg_instance: ASGInstance, hook_name: str, gha: GitHubActions
):
    asg = ASG(asg_name=asg_instance.asg_name)
    instance_id = asg_instance.instance_id
    try:
        registration_token_secret_prefix = environ["REGISTRATION_TOKEN_SECRET_PREFIX"]
        registration_token_secret = f"{registration_token_secret_prefix}-{instance_id}"
        # if the instance is already registered, we don't need the token
        gha.ensure_registration_token(
            registration_token_secret,
            present=gha.find_runner_by_label(f"instance_id:{instance_id}") is None,
        )
        asg.complete_lifecycle_action(hook_name=hook_name, instance_id=instance_id)
        LOG.info(
            f"Lifecycle hook %s for %s is successfully complete.",
            hook_name,
            instance_id,
        )

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
            instance_id=instance_id,
        )
        LOG.info(
            f"Lifecycle hook %s for %s is complete with ABANDON result.",
            hook_name,
            instance_id,
        )


def _handle_bootstrap_hook(
    asg_instance: ASGInstance, hook_name: str, gha: GitHubActions, wait_timeout=900
):
    instance_id = asg_instance.instance_id
    asg = ASG(asg_name=asg_instance.asg_name)
    label = f"instance_id:{instance_id}"
    LOG.info("Looking for runner with label %s.", label)
    runner = gha.find_runner_by_label(label)
    if runner:
        result = "CONTINUE"
        try:
            LOG.info("Found runner %s in GitHub.", runner.name)

        finally:
            asg.complete_lifecycle_action(
                hook_name=hook_name, result=result, instance_id=instance_id
            )
            LOG.info(
                f"Lifecycle hook %s for %s is complete with result %s.",
                hook_name,
                instance_id,
                result,
            )
    else:
        LOG.warning(
            "Couldn't find a runner labeled %s. "
            "It can be OK if the runner is provisioning the first time. "
            "Then, puppet will complete the bootstrap hook.",
            label,
        )


def _get_github_token(org):
    return (
        get_secret(boto3.client("secretsmanager"), environ["GITHUB_SECRET"])
        if environ["GITHUB_SECRET_TYPE"] == "token"
        else get_tmp_token(int(environ["GH_APP_ID"]), environ["GITHUB_SECRET"], org)
    )
