"""
Unit tests of how the module's three Lambdas get a GitHub token, and of what registration does
without one (SEC-46).

They need no AWS account and no GitHub: infrahouse-core's token source and signers are
replaced with mocks before a Lambda's ``main.py`` is imported, because the Lambda builds the
``kms`` source at import time.
"""

import importlib.util
import logging
from os import path as osp
from types import ModuleType, SimpleNamespace
from typing import Dict, Iterator
from unittest import mock

import pytest
import requests
from botocore.exceptions import ClientError, EndpointConnectionError
from github import GithubException
from infrahouse_core.aws.exceptions import IHSecretNotFound

REPO_ROOT = osp.abspath(osp.join(osp.dirname(__file__), ".."))

GH_APP_ID = "1016363"
GITHUB_ORG_NAME = "infrahouse"
TOKEN_SECRET_ARN = (
    "arn:aws:secretsmanager:us-west-2:123456789012:secret:GITHUB_TOKEN-AbCdEf"
)
PEM_SECRET_ARN = (
    "arn:aws:secretsmanager:us-west-2:123456789012:secret:github-app-pem-AbCdEf"
)
KMS_KEY_ARN = (
    "arn:aws:kms:us-west-2:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab"
)
GITHUB_TOKEN = "ghs_not-a-real-installation-token"

# The permissions each Lambda's installation token gets: GitHub's "Self-hosted runners"
# organization permission. Listing and getting runners need read; a registration token and
# deleting a runner need write.
EXPECTED_PERMISSIONS = {
    # Lists runners and requests registration tokens.
    "runner_registration": {"organization_self_hosted_runners": "write"},
    # Lists runners and deletes them.
    "runner_deregistration": {"organization_self_hosted_runners": "write"},
    # Lists runners only.
    "record_metric": {"organization_self_hosted_runners": "read"},
}
LAMBDAS = sorted(EXPECTED_PERMISSIONS)

# The environment Terraform gives each Lambda, by GITHUB_SECRET_TYPE.
ENVIRONMENTS = {
    "token": {"GITHUB_SECRET_TYPE": "token", "GITHUB_SECRET": TOKEN_SECRET_ARN},
    "pem": {
        "GITHUB_SECRET_TYPE": "pem",
        "GITHUB_SECRET": PEM_SECRET_ARN,
        "GH_APP_ID": GH_APP_ID,
    },
    "kms": {
        "GITHUB_SECRET_TYPE": "kms",
        "GITHUB_APP_KMS_KEY_ARN": KMS_KEY_ARN,
        "GH_APP_ID": GH_APP_ID,
    },
}
CREDENTIAL_VARIABLES = {
    name for environment in ENVIRONMENTS.values() for name in environment
}


@pytest.fixture
def core_mocks() -> Iterator[SimpleNamespace]:
    """
    Replace infrahouse-core's token source, signers and ``get_secret`` with mocks.

    :return: The mocks: ``source``, ``kms_signer``, ``pem_signer`` and ``get_secret``.
    """
    with mock.patch(
        "infrahouse_core.github.GitHubAppTokenSource"
    ) as source, mock.patch(
        "infrahouse_core.github.KMSSigner"
    ) as kms_signer, mock.patch(
        "infrahouse_core.github.PEMSecretSigner"
    ) as pem_signer, mock.patch(
        "infrahouse_core.aws.get_secret", return_value=GITHUB_TOKEN
    ) as get_secret:
        source.return_value.installation_token.return_value = GITHUB_TOKEN
        yield SimpleNamespace(
            source=source,
            kms_signer=kms_signer,
            pem_signer=pem_signer,
            get_secret=get_secret,
        )


def _load_lambda(
    lambda_name: str, environment: Dict[str, str], monkeypatch
) -> ModuleType:
    """
    Import a Lambda's ``main.py`` as a new module, with the given environment.

    Each call imports the file again, so module-scope state doesn't leak between tests.

    :param lambda_name: The sub-module the Lambda belongs to, e.g. ``record_metric``.
    :param environment: The GitHub credential variables Terraform would set.
    :param monkeypatch: pytest's monkeypatch fixture, to set the environment.
    :return: The imported module.
    """
    for name in CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    # boto3 clients need a region but no credentials or network to be created.
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    path = osp.join(REPO_ROOT, "modules", lambda_name, "lambda", "main.py")
    spec = importlib.util.spec_from_file_location(f"{lambda_name}_lambda_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("lambda_name", LAMBDAS)
@pytest.mark.parametrize(
    "secret_type, signer_name, credential_arn",
    [("kms", "kms_signer", KMS_KEY_ARN), ("pem", "pem_signer", PEM_SECRET_ARN)],
    ids=["kms", "pem"],
)
def test_app_token_is_down_scoped(
    lambda_name, secret_type, signer_name, credential_arn, core_mocks, monkeypatch
):
    """A GitHub App Lambda signs with its mode's signer and asks for only the permissions it needs."""
    module = _load_lambda(lambda_name, ENVIRONMENTS[secret_type], monkeypatch)

    assert module._get_github_token(GITHUB_ORG_NAME) == GITHUB_TOKEN

    signer = getattr(core_mocks, signer_name)
    other_signer = (
        core_mocks.pem_signer if secret_type == "kms" else core_mocks.kms_signer
    )
    signer.assert_called_once_with(credential_arn, session=module._session)
    other_signer.assert_not_called()
    core_mocks.source.assert_called_once_with(int(GH_APP_ID), signer.return_value)
    core_mocks.source.return_value.installation_token.assert_called_once_with(
        GITHUB_ORG_NAME, permissions=EXPECTED_PERMISSIONS[lambda_name]
    )
    core_mocks.get_secret.assert_not_called()


@pytest.mark.parametrize("lambda_name", LAMBDAS)
def test_kms_token_source_is_built_once(lambda_name, core_mocks, monkeypatch):
    """A warm kms Lambda reuses the token source and signer it built at import, and so the KMS client."""
    module = _load_lambda(lambda_name, ENVIRONMENTS["kms"], monkeypatch)
    assert core_mocks.kms_signer.call_count == 1

    module._get_github_token(GITHUB_ORG_NAME)
    module._get_github_token(GITHUB_ORG_NAME)

    assert core_mocks.source.call_count == 1
    assert core_mocks.kms_signer.call_count == 1
    assert core_mocks.source.return_value.installation_token.call_count == 2


@pytest.mark.parametrize("lambda_name", LAMBDAS)
def test_pem_signer_is_built_per_token(lambda_name, core_mocks, monkeypatch):
    """
    A pem Lambda builds a signer for each token, so an updated PEM secret takes effect on the
    next token: a signer keeps the PEM it read.
    """
    module = _load_lambda(lambda_name, ENVIRONMENTS["pem"], monkeypatch)
    core_mocks.pem_signer.assert_not_called()

    module._get_github_token(GITHUB_ORG_NAME)
    module._get_github_token(GITHUB_ORG_NAME)

    assert (
        core_mocks.pem_signer.call_args_list
        == [mock.call(PEM_SECRET_ARN, session=module._session)] * 2
    )
    assert core_mocks.source.call_count == 2
    assert core_mocks.source.return_value.installation_token.call_count == 2
    core_mocks.kms_signer.assert_not_called()


@pytest.mark.parametrize("lambda_name", LAMBDAS)
def test_token_mode_reads_the_secret(lambda_name, core_mocks, monkeypatch):
    """With a classic token, the Lambda reads the secret as before and builds no App source."""
    module = _load_lambda(lambda_name, ENVIRONMENTS["token"], monkeypatch)

    assert module._get_github_token(GITHUB_ORG_NAME) == GITHUB_TOKEN
    core_mocks.get_secret.assert_called_once_with(
        module._secretsmanager, TOKEN_SECRET_ARN
    )
    core_mocks.source.assert_not_called()
    core_mocks.kms_signer.assert_not_called()
    core_mocks.pem_signer.assert_not_called()


@pytest.mark.parametrize("lambda_name", LAMBDAS)
def test_unknown_secret_type_fails_at_import(lambda_name, core_mocks, monkeypatch):
    """A credential type the Lambda doesn't know stops it at start instead of being taken for a PEM."""
    with pytest.raises(ValueError, match="GITHUB_SECRET_TYPE"):
        _load_lambda(lambda_name, {"GITHUB_SECRET_TYPE": "jwt"}, monkeypatch)
    core_mocks.source.assert_not_called()


@pytest.mark.parametrize("lambda_name", LAMBDAS)
@pytest.mark.parametrize("secret_type", ["token", "pem", "kms"])
def test_token_is_not_logged(lambda_name, secret_type, core_mocks, monkeypatch, caplog):
    """Getting a token logs nothing that contains it, at any level."""
    module = _load_lambda(lambda_name, ENVIRONMENTS[secret_type], monkeypatch)

    with caplog.at_level(logging.DEBUG):
        module._get_github_token(GITHUB_ORG_NAME)

    assert GITHUB_TOKEN not in caplog.text


INSTANCE_ID = "i-0123456789abcdef0"
ASG_NAME = "actions-runner-asg"


def _lifecycle_event(hook_name: str) -> dict:
    """
    Return the EventBridge event of a launch lifecycle action, as the registration Lambda gets it.

    :param hook_name: The lifecycle hook's name: ``registration`` or ``bootstrap``.
    :return: The event.
    """
    return {"detail": {"LifecycleHookName": hook_name, "EC2InstanceId": INSTANCE_ID}}


def _mock_asg(module: ModuleType, monkeypatch) -> mock.MagicMock:
    """
    Replace the registration Lambda's ``ASGInstance``, ``ASG`` and hook handlers with mocks.

    :param module: The imported registration Lambda.
    :param monkeypatch: pytest's monkeypatch fixture.
    :return: The ``ASG`` class mock; its ``return_value`` is the ASG.
    """
    asg_instance = mock.MagicMock(instance_id=INSTANCE_ID, asg_name=ASG_NAME)
    monkeypatch.setattr(
        module, "ASGInstance", mock.MagicMock(return_value=asg_instance)
    )
    asg_class = mock.MagicMock()
    monkeypatch.setattr(module, "ASG", asg_class)
    monkeypatch.setattr(module, "_handle_registration_hook", mock.MagicMock())
    monkeypatch.setattr(module, "_handle_bootstrap_hook", mock.MagicMock())
    return asg_class


# What a pool's first token can fail with, by credential type.
TOKEN_FAILURES = [
    (
        "kms",
        ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "The key policy doesn't allow kms:Sign.",
                }
            },
            "Sign",
        ),
    ),
    (
        "kms",
        EndpointConnectionError(endpoint_url="https://kms.us-west-2.amazonaws.com/"),
    ),
    ("kms", GithubException(401, {"message": "A JSON web token could not be decoded"})),
    ("kms", requests.HTTPError("401 Client Error: Unauthorized for url")),
    ("kms", RuntimeError("The GitHub App has no installation in infrahouse")),
    ("pem", IHSecretNotFound(f"Secret not found: {PEM_SECRET_ARN}")),
    (
        "pem",
        GithubException(
            422,
            {
                "message": "The permissions requested are not granted to this installation."
            },
        ),
    ),
    (
        "token",
        ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "No such secret.",
                }
            },
            "GetSecretValue",
        ),
    ),
]
TOKEN_FAILURE_IDS = [
    "kms-sign-denied",
    "kms-unreachable",
    "github-401",
    "installation-lookup-http-error",
    "no-installation",
    "pem-secret-missing",
    "github-422",
    "token-secret-missing",
]


@pytest.mark.parametrize("secret_type, error", TOKEN_FAILURES, ids=TOKEN_FAILURE_IDS)
def test_registration_abandons_without_token(
    secret_type, error, core_mocks, monkeypatch
):
    """
    When the registration hook can't get a token, its lifecycle action is completed with
    ABANDON at once, and the error propagates, so the invocation fails and the alarm fires.
    """
    module = _load_lambda("runner_registration", ENVIRONMENTS[secret_type], monkeypatch)
    monkeypatch.setenv("GITHUB_ORG_NAME", GITHUB_ORG_NAME)
    asg_class = _mock_asg(module, monkeypatch)
    core_mocks.source.return_value.installation_token.side_effect = error
    core_mocks.get_secret.side_effect = error

    with pytest.raises(type(error)) as raised:
        module.lambda_handler(_lifecycle_event("registration"), None)

    assert raised.value is error
    asg_class.assert_called_once_with(asg_name=ASG_NAME, session=module._session)
    asg_class.return_value.complete_lifecycle_action.assert_called_once_with(
        hook_name="registration", result="ABANDON", instance_id=INSTANCE_ID
    )
    module._handle_registration_hook.assert_not_called()


def test_bootstrap_leaves_its_action_without_token(core_mocks, monkeypatch):
    """
    The bootstrap hook's action is Puppet's to complete: when the Lambda can't get a token,
    the error propagates, and the Lambda completes no action.
    """
    module = _load_lambda("runner_registration", ENVIRONMENTS["kms"], monkeypatch)
    monkeypatch.setenv("GITHUB_ORG_NAME", GITHUB_ORG_NAME)
    asg_class = _mock_asg(module, monkeypatch)
    error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "Denied."}}, "Sign"
    )
    core_mocks.source.return_value.installation_token.side_effect = error

    with pytest.raises(ClientError):
        module.lambda_handler(_lifecycle_event("bootstrap"), None)

    asg_class.assert_not_called()
    module._handle_bootstrap_hook.assert_not_called()
