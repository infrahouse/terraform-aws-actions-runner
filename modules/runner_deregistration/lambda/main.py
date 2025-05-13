import logging
from os import environ
from botocore.exceptions import ClientError
from infrahouse_core.aws.asg_instance import ASGInstance
from infrahouse_core.github import GitHubActions, get_tmp_token, GitHubAuth
from infrahouse_core.aws import get_secret

import boto3
from infrahouse_core.aws.asg import ASG

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)

HOOK_DEREGISTRATION = "deregistration"


def lambda_handler(event, context):
    LOG.info(f"{event = }")
    github = GitHubAuth(
        _get_github_token(environ["GITHUB_ORG_NAME"]), environ["GITHUB_ORG_NAME"]
    )
    gha = GitHubActions(github)

    if event["detail"].get("LifecycleHookName") == HOOK_DEREGISTRATION:
        """
        This even is received when an instance is about to be terminated or when it's re-entering the warm pool.
        """
        _handle_deregistration_hook(
            HOOK_DEREGISTRATION, event["detail"]["EC2InstanceId"]
        )
    else:
        # Fall back to sweeping unused runners if no lifecycle hook is present
        _clean_runners(gha, environ["INSTALLATION_ID"])


def _handle_deregistration_hook(hook_name, instance_id):
    asg_instance = ASGInstance(instance_id=instance_id)
    asg = ASG(asg_name=asg_instance.asg_name)
    result = "ABANDON"
    try:
        if asg_instance.lifecycle_state == "Warmed:Terminating:Wait":
            # If the instance is terminating don't stop actions-runner, just complete the lifecycle
            result = "CONTINUE"
            return

        exit_code = asg_instance.execute_command(
            "/usr/bin/systemctl stop actions-runner.service",
        )[0]
        result = "CONTINUE" if exit_code == 0 else "ABANDON"

    except TimeoutError as err:
        LOG.error(err)
        result = "ABANDON"

    finally:
        asg.complete_lifecycle_action(
            hook_name=HOOK_DEREGISTRATION, result=result, instance_id=instance_id
        )
        LOG.info(
            "Lifecycle hook %s for %s is complete with result %s.",
            hook_name,
            instance_id,
            result,
        )


def _get_github_token(org):
    return (
        get_secret(boto3.client("secretsmanager"), environ["GITHUB_SECRET"])
        if environ["GITHUB_SECRET_TYPE"] == "token"
        else get_tmp_token(int(environ["GH_APP_ID"]), environ["GITHUB_SECRET"], org)
    )


def _clean_runners(gha: GitHubActions, installation_id: str):
    """
    Deregister GitHub Actions runners that are not running anymore (e.g. terminated).
    Deregister only runners labeled with 'installation_id:<installation_id>'.

    :param gha: GitHubActions object
    :param installation_id: unique ID of the runners installed by the module.
        Each runner has a label 'installation_id:<installation_id>'.
    """
    for runner in gha.find_runners_by_label(f"installation_id:{installation_id}"):
        LOG.info("Found runner %s", runner.name)
        try:
            if ASGInstance(runner.instance_id).state == "terminated":
                LOG.info(
                    "Instance %s is terminated. Will deregister the runner %s.",
                    runner.instance_id,
                    runner.name,
                )
                gha.deregister_runner(runner)
        except IndexError:
            LOG.info(
                "ASG lookup failed for instance %s (likely terminated). Will deregister the runner %s.",
                runner.instance_id,
                runner.name,
            )
            gha.deregister_runner(runner)

        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
                LOG.info(
                    "Instance %s doesn't exist. Will deregister the runner %s.",
                    runner.instance_id,
                    runner.name,
                )
                gha.deregister_runner(runner)
            else:
                raise  # re-raise for other unexpected errors
