import logging
from os import environ
from typing import Optional

from infrahouse_core.github import (
    GitHubActions,
    GitHubAppTokenSource,
    GitHubAuth,
    GitHubTokenSource,
    KMSSigner,
    PEMSecretSigner,
)

from botocore.exceptions import ClientError, BotoCoreError
from infrahouse_core.aws import get_secret
from infrahouse_core.aws.asg import ASG
from infrahouse_core.aws.asg_instance import ASGInstance
from infrahouse_core.exceptions import IHCoreException
import boto3
from github import GithubException
import requests

LOG = logging.getLogger()
LOG.setLevel(level=logging.INFO)

# Module-scope boto3 session: created once at cold start so the ~8 MB
# botocore endpoints.json parse runs during INIT (uncapped CPU) instead of
# inside the handler. Passed explicitly to every ASGInstance / ASG so all
# AWS clients share a single credential chain and endpoint cache.
_session = boto3.Session()
_secretsmanager = _session.client("secretsmanager")

HOOK_REGISTRATION = "registration"
HOOK_BOOTSTRAP = "bootstrap"

# The permissions of the installation token, when the function authenticates as a GitHub App.
# It lists the organization's runners (read) and requests runner registration tokens (write):
# both are GitHub's "Self-hosted runners" organization permission.
GITHUB_TOKEN_PERMISSIONS = {"organization_self_hosted_runners": "write"}

# What getting a GitHub token can raise: an AWS error (Secrets Manager, or kms:Sign denied or
# unreachable), a missing secret (IHSecretNotFound), GitHub refusing the JWT or the token
# request (GithubException), an HTTP or network error in the App's installation lookup, or no
# installation of the App in the organization (RuntimeError).
GITHUB_TOKEN_ERRORS = (
    ClientError,
    BotoCoreError,
    IHCoreException,
    GithubException,
    requests.RequestException,
    RuntimeError,
    TimeoutError,
)


def _build_token_source(secret_type: str) -> Optional[GitHubTokenSource]:
    """
    Build the GitHub App token source that lasts for the life of the execution environment.

    Only ``kms`` mode has one: the KMS signer holds no key, so a warm function reuses it and
    its KMS client. A PEM signer keeps the PEM it reads for its own life, so in ``pem`` mode
    ``_pem_token_source`` builds a source per token, and an updated secret takes effect on
    the next token.

    :param secret_type: ``GITHUB_SECRET_TYPE``: ``token``, ``pem`` or ``kms``.
    :return: The ``kms`` token source, or ``None`` for ``token`` and ``pem``.
    :raises ValueError: If the credential type is unknown.
    """
    if secret_type == "kms":
        return GitHubAppTokenSource(
            int(environ["GH_APP_ID"]),
            KMSSigner(environ["GITHUB_APP_KMS_KEY_ARN"], session=_session),
        )
    if secret_type in ("token", "pem"):
        return None
    raise ValueError(
        f"Unknown GITHUB_SECRET_TYPE {secret_type!r}: expected token, pem or kms."
    )


def _pem_token_source() -> GitHubTokenSource:
    """
    Build a GitHub App token source with a new signer, which reads the PEM secret afresh.

    :return: The token source for one token.
    """
    return GitHubAppTokenSource(
        int(environ["GH_APP_ID"]),
        PEMSecretSigner(environ["GITHUB_SECRET"], session=_session),
    )


_token_source = _build_token_source(environ["GITHUB_SECRET_TYPE"])


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
    asg_instance = ASGInstance(
        instance_id=event["detail"]["EC2InstanceId"], session=_session
    )
    if hook_name == HOOK_REGISTRATION:
        github_token = _get_github_token_or_abandon(asg_instance, hook_name)
    else:
        github_token = _get_github_token(environ["GITHUB_ORG_NAME"])
    github = GitHubAuth(github_token, environ["GITHUB_ORG_NAME"])
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


def _get_github_token_or_abandon(asg_instance: ASGInstance, hook_name: str) -> str:
    """
    Return a GitHub token for the registration hook, or abandon the instance's lifecycle action.

    Without a token the instance can't register, so the action is completed with ``ABANDON``
    at once instead of waiting out the hook's heartbeat. The error is then re-raised: the
    invocation fails, and the function's error alarm fires.

    :param asg_instance: The instance the lifecycle action is for.
    :param hook_name: The lifecycle hook's name.
    :return: The GitHub token.
    :raises Exception: What getting the token raised, one of ``GITHUB_TOKEN_ERRORS``.
    """
    try:
        return _get_github_token(environ["GITHUB_ORG_NAME"])
    except GITHUB_TOKEN_ERRORS as err:
        LOG.error(
            "Couldn't get a GitHub token: %s. Abandoning lifecycle hook %s for %s.",
            err,
            hook_name,
            asg_instance.instance_id,
        )
        ASG(asg_name=asg_instance.asg_name, session=_session).complete_lifecycle_action(
            hook_name=hook_name,
            result="ABANDON",
            instance_id=asg_instance.instance_id,
        )
        raise


def _handle_registration_hook(
    asg_instance: ASGInstance, hook_name: str, gha: GitHubActions
):
    asg = ASG(asg_name=asg_instance.asg_name, session=_session)
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
    asg = ASG(asg_name=asg_instance.asg_name, session=_session)
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


def _get_github_token(org: str) -> str:
    """
    Return a GitHub token for the organization.

    :param org: The GitHub organization.
    :return: The classic token from its secret, or an installation token of the GitHub App
        with ``GITHUB_TOKEN_PERMISSIONS``.
    """
    secret_type = environ["GITHUB_SECRET_TYPE"]
    if secret_type == "token":
        return get_secret(_secretsmanager, environ["GITHUB_SECRET"])
    token_source = _pem_token_source() if secret_type == "pem" else _token_source
    return token_source.installation_token(org, permissions=GITHUB_TOKEN_PERMISSIONS)
