import logging
from os import environ
from botocore.exceptions import ClientError
from infrahouse_core.aws.asg_instance import ASGInstance
from infrahouse_core.github import GitHubActions, get_tmp_token, GitHubAuth
from infrahouse_core.aws import get_secret

import boto3

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)

# Module-scope boto3 session: created once at cold start so the ~8 MB
# botocore endpoints.json parse runs during INIT (uncapped CPU) instead of
# inside the handler. Passed explicitly to every ASGInstance so all
# AWS clients share a single credential chain and endpoint cache.
_session = boto3.Session()
_secretsmanager = _session.client("secretsmanager")
_ssm = _session.client("ssm")
_autoscaling = _session.client("autoscaling")

HOOK_DEREGISTRATION = "deregistration"


def lambda_handler(event, context):
    LOG.info(f"{event = }")
    github = GitHubAuth(
        _get_github_token(environ["GITHUB_ORG_NAME"]), environ["GITHUB_ORG_NAME"]
    )
    gha = GitHubActions(github)

    if event["detail"].get("LifecycleHookName") == HOOK_DEREGISTRATION:
        """
        Received when an instance is entering Terminating:Wait (either a
        regular scale-in / ASG termination, or a warm-pool trim).
        """
        instance_id = event["detail"]["EC2InstanceId"]
        # Safety-net cleanup of the registration token secret. Puppet deletes
        # it right after register (fast path); this call is idempotent and
        # covers the case where Puppet never converged (crash during bootstrap,
        # instance killed before agent run, etc).
        gha.ensure_registration_token(
            f"{environ['REGISTRATION_TOKEN_SECRET_PREFIX']}-{instance_id}",
            present=False,
        )
        _handle_deregistration_hook(instance_id)
    else:
        # Fall back to sweeping unused runners if no lifecycle hook is present
        _clean_runners(gha, environ["INSTALLATION_ID"])


def _handle_deregistration_hook(instance_id):
    """Fire-and-forget scale-in helper.

    Two paths:

    - ``Warmed:Terminating:Wait``: warm-pool trim. No runner service is
      running on a hibernated warm-pool instance, so there's nothing to
      stop. Complete the lifecycle hook immediately.
    - ``Terminating:Wait``: dispatch an SSM
      ``systemctl stop actions-runner.service`` command and return. We
      do NOT wait for SSM to deliver the command, and we do NOT complete
      the lifecycle action — the on-host ``ExecStopPost`` script owns
      that once the runner exits gracefully (see puppet-code
      ``gha-on-runner-exit.sh``). The on-host heartbeater keeps the
      hook alive for long-running jobs.

    Any other lifecycle state is unexpected for a deregistration event;
    the SSM stop still fires but is effectively a no-op.
    """
    asg_instance = ASGInstance(instance_id=instance_id, session=_session)
    asg_name = asg_instance.asg_name

    if asg_instance.lifecycle_state == "Warmed:Terminating:Wait":
        _autoscaling.complete_lifecycle_action(
            LifecycleHookName=HOOK_DEREGISTRATION,
            AutoScalingGroupName=asg_name,
            InstanceId=instance_id,
            LifecycleActionResult="CONTINUE",
        )
        LOG.info(
            "Warm-pool trim for %s — completed lifecycle hook immediately.",
            instance_id,
        )
        return

    try:
        _ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": ["/usr/bin/systemctl stop actions-runner.service"]},
        )
    except ClientError as err:
        LOG.error(
            "Failed to send SSM stop to %s: %s. "
            "Lifecycle hook will time out after heartbeat_timeout and ABANDON.",
            instance_id,
            err,
        )
        raise
    LOG.info(
        "Sent SSM stop for actions-runner.service on %s. "
        "ExecStopPost will complete the lifecycle hook.",
        instance_id,
    )


def _get_github_token(org):
    return (
        get_secret(_secretsmanager, environ["GITHUB_SECRET"])
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
            if (
                ASGInstance(instance_id=runner.instance_id, session=_session).state
                == "terminated"
            ):
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
