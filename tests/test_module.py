import configparser
import json
import os
import platform
import re
import subprocess
import sys
from os import path as osp, remove
from pathlib import Path
from shutil import copytree, ignore_patterns, rmtree
from textwrap import dedent
from typing import Any, Dict, List, Optional

import boto3
import pytest
from infrahouse_core.github import GitHubActions, GitHubAuth
from pytest_infrahouse import terraform_apply

from tests.conftest import (
    LOG,
    TERRAFORM_ROOT_DIR,
    GITHUB_ORG_NAME,
    assert_deregistration_token_deletes_runner,
    assert_error_rate_alarm,
    assert_immediate_error_alarm,
    assert_lambda_memory_within_limit,
    assert_lambda_roles_sign_only,
    assert_lambdas_use_kms_key,
    assert_no_inspector_exclusion_tag,
    assert_record_metric_publishes,
    assert_role_name_prefixes,
    assert_role_outputs,
    ensure_runners,
    get_tmp_token,
    invoke_deregistration_sweep,
    log_record_metric_token_times,
    DEREGISTRATION_ERROR_RATE_PERIOD,
    GH_APP_ID,
    RECORD_METRIC_ERROR_RATE_PERIOD,
)


@pytest.mark.parametrize("aws_provider_version", ["~> 6.0"], ids=["aws-6"])
@pytest.mark.parametrize(
    "secret_type, ubuntu_codename",
    [
        ("token", "noble"),
        # SEC-46: the GitHub App's key in a KMS key, imported fresh on every run.
        ("kms", "noble"),
    ],
)
def test_module(
    request,
    service_network,
    test_role_arn,
    keep_after,
    github_token,
    github_app_pem_secret_arn,
    secret_type,
    ubuntu_codename,
    aws_region,
    boto3_session,
    aws_provider_version,
):
    subnet_public_ids = service_network["subnet_public_ids"]["value"]
    subnet_private_ids = service_network["subnet_private_ids"]["value"]

    # Every case checks the runners on GitHub with the token, whatever the pool authenticates with.
    assert github_token, "Set GitHub token value with --github-token CLI argument."
    if secret_type == "pem":
        assert (
            github_app_pem_secret_arn
        ), "Set a secret ARN containing GitHub APP PEM key token value with --github-app-pem-secret CLI argument."
    # Only the kms case creates a key, so the fixture is requested here rather than as an argument.
    kms_key_arn = (
        request.getfixturevalue("github_app_kms_key_arn")
        if secret_type == "kms"
        else None
    )

    terraform_module_dir = osp.join(TERRAFORM_ROOT_DIR, "actions-runner")

    # Clean up state files to ensure fresh terraform init
    state_files = [
        osp.join(terraform_module_dir, ".terraform"),
        osp.join(terraform_module_dir, ".terraform.lock.hcl"),
    ]
    for state_file in state_files:
        try:
            if osp.isdir(state_file):
                rmtree(state_file)
            elif osp.isfile(state_file):
                remove(state_file)
        except FileNotFoundError:
            pass

    # Generate terraform.tf with specified AWS provider version
    with open(osp.join(terraform_module_dir, "terraform.tf"), "w") as fp:
        fp.write(dedent(f"""
                terraform {{
                  required_version = "~> 1.5"
                  //noinspection HILUnresolvedReference
                  required_providers {{
                    aws = {{
                      source = "hashicorp/aws"
                      version = "{aws_provider_version}"
                    }}
                  }}
                }}
                """))

    with open(osp.join(terraform_module_dir, "terraform.tfvars"), "w") as fp:
        asg_max_size = 2
        fp.write(dedent(f"""
                    region          = "{aws_region}"
                    github_org_name = "{GITHUB_ORG_NAME}"
                    ubuntu_codename = "{ubuntu_codename}"

                    subnet_ids         = {json.dumps(subnet_public_ids)}
                    lambda_subnet_ids  = {json.dumps(subnet_private_ids)}
                    architecture       = "{platform.machine()}"
                    python_version     = "python{sys.version_info.major}.{sys.version_info.minor}"
                    asg_max_size = {asg_max_size}
                    """))
        if test_role_arn:
            fp.write(dedent(f"""
                    role_arn        = "{test_role_arn}"
                    """))

        if secret_type == "token":
            fp.write(f'github_token = "{github_token}"')
        elif secret_type == "kms":
            # No token or PEM for the module: its Lambdas sign with the key only.
            fp.write(dedent(f"""
                github_app_kms_key_arn = "{kms_key_arn}"
                github_app_id = {GH_APP_ID}
                """))
        else:
            fp.write(dedent(f"""
                github_app_pem_secret_arn = "{github_app_pem_secret_arn}"
                github_app_id = {GH_APP_ID}
                """))

    with terraform_apply(
        terraform_module_dir,
        destroy_after=not keep_after,
        json_output=True,
    ) as tf_output:
        LOG.info("%s", json.dumps(tf_output, indent=4))
        gha = GitHubActions(GitHubAuth(github_token, GITHUB_ORG_NAME))
        try:
            # Issue #109: a policy must be able to select the Lambda roles by name
            # pattern without also selecting the instance role. Issue #111: the
            # instance role is named from the same <pool> as the Lambda roles.
            asg_name = tf_output["autoscaling_group_name"]["value"]
            lambda_role_arns = assert_role_name_prefixes(
                boto3_session,
                aws_region,
                tf_output["runner_role_arn"]["value"],
                asg_name,
                {
                    "reg": tf_output["registration_lambda_name"]["value"],
                    "dereg": tf_output["deregistration_lambda_name"]["value"],
                    "metric": tf_output["record_metric_lambda_name"]["value"],
                },
            )
            assert_role_outputs(boto3_session, tf_output, asg_name, lambda_role_arns)

            # Issue #91: each Lambda's error alarm must be able to fire at the rate the
            # function runs. Registration runs on launch events, deregistration every 30
            # minutes and record_metric every minute.
            cloudwatch_client = boto3_session.client(
                "cloudwatch", region_name=aws_region
            )
            registration_lambda_name = tf_output["registration_lambda_name"]["value"]
            deregistration_lambda_name = tf_output["deregistration_lambda_name"][
                "value"
            ]
            record_metric_lambda_name = tf_output["record_metric_lambda_name"]["value"]
            assert_immediate_error_alarm(cloudwatch_client, registration_lambda_name)
            assert_error_rate_alarm(
                cloudwatch_client,
                deregistration_lambda_name,
                DEREGISTRATION_ERROR_RATE_PERIOD,
            )
            assert_error_rate_alarm(
                cloudwatch_client,
                record_metric_lambda_name,
                RECORD_METRIC_ERROR_RATE_PERIOD,
            )

            autoscaling_client = boto3_session.client(
                "autoscaling", region_name=aws_region
            )
            ensure_runners(
                gha,
                aws_region,
                autoscaling_client,
                timeout_time=900
                + asg_max_size
                * 900,  # 300 seconds to provision, 300 - warmup, 300 - cooldown old.
                test_role_arn=test_role_arn,
            )

            # Runners only reach "online" after the bootstrap script signals the
            # lifecycle hook, and the exclusion tag is dropped just before that
            # signal. A registered runner still carrying the tag means Inspector
            # is not scanning an instance that is already taking jobs.
            assert_no_inspector_exclusion_tag(
                gha,
                aws_region,
                test_role_arn=test_role_arn,
            )

            # runner_registration and record_metric fire naturally while the ASG
            # comes up (launch lifecycle hook and 1-minute schedule, respectively).
            # runner_deregistration's OOM-prone path is the _clean_runners sweep
            # that normally runs on a 30-minute schedule, so the test has to
            # invoke it explicitly to get coverage on the real failure mode.
            lambda_client = boto3_session.client("lambda", region_name=aws_region)
            invoke_deregistration_sweep(lambda_client, deregistration_lambda_name)

            # record_metric publishes the runner counts that drive autoscaling, with the token it
            # gets under a five-second timeout. Terraform creates it after the ASG, so it has only
            # just started to run every minute: wait for a few runs.
            assert_record_metric_publishes(
                cloudwatch_client, asg_name, record_metric_lambda_name
            )
            log_record_metric_token_times(
                boto3_session, aws_region, record_metric_lambda_name
            )

            if secret_type == "kms":
                # SEC-46: a kms pool's Lambdas sign with the key, with RS256's algorithm only,
                # and can read neither a token nor the PEM.
                assert_lambdas_use_kms_key(
                    lambda_client,
                    [
                        registration_lambda_name,
                        deregistration_lambda_name,
                        record_metric_lambda_name,
                    ],
                    kms_key_arn,
                )
                assert_lambda_roles_sign_only(
                    boto3_session,
                    lambda_role_arns,
                    kms_key_arn,
                    github_app_pem_secret_arn,
                )

            for lambda_name in (
                registration_lambda_name,
                deregistration_lambda_name,
                record_metric_lambda_name,
            ):
                assert_lambda_memory_within_limit(cloudwatch_client, lambda_name)

            if secret_type == "kms":
                # The deregistration Lambda deletes a runner only when its instance is gone,
                # which a test run may not get to. A token minted as that Lambda mints it
                # deletes one runner here, so the down-scoped permission covers that call too.
                assert_deregistration_token_deletes_runner(boto3_session, kms_key_arn)
        finally:
            if not keep_after:

                # Delete secrets with the registration token prefix
                registration_token_secret_prefix = tf_output[
                    "registration_token_secret_prefix"
                ]["value"]
                secrets_client = boto3_session.client(
                    "secretsmanager", region_name=aws_region
                )
                paginator = secrets_client.get_paginator("list_secrets")
                for page in paginator.paginate():
                    for secret in page["SecretList"]:
                        if secret["Name"].startswith(registration_token_secret_prefix):
                            LOG.info(f"Deleting secret: {secret['Name']}")
                            secrets_client.delete_secret(
                                SecretId=secret["Name"], ForceDeleteWithoutRecovery=True
                            )

                for runner in gha.find_runners_by_label("awesome"):
                    gha.deregister_runner(runner)


# The input validation tests below need no AWS account: Terraform rejects an invalid
# literal module argument while validating the configuration, which `terraform plan`
# does before it configures any provider. The environment is stripped of AWS settings
# so that a missing validation fails the test instead of reaching an account.
REPO_ROOT = osp.abspath(osp.join(osp.dirname(__file__), ".."))
DEFAULT_PYTHON_VERSION = "python3.12"
# Text only this module's validation rule produces. lambda-monitored validates python_version
# too, with the same "Invalid value for variable" summary, so the test looks for this to
# prove the root module rejects it.
PYTHON_VERSION_ERROR = "python_version must be python3.12 or python3.13. Got"

# SEC-46: text only this module's credential checks produce. The rules that compare inputs are
# preconditions, which Terraform evaluates at plan; KMS_KEY_ARN_ERROR is a variable validation.
CREDENTIALS_NONE_ERROR = "No GitHub credential is set."
CREDENTIALS_KMS_COMBINED_ERROR = "github_app_kms_key_arn can't be combined with"
CREDENTIALS_APP_ID_ERROR = "github_app_id is required"
KMS_KEY_ARN_ERROR = "github_app_kms_key_arn must be a KMS key ARN"
MODULE_INPUT_ERRORS = [
    PYTHON_VERSION_ERROR,
    CREDENTIALS_NONE_ERROR,
    CREDENTIALS_KMS_COMBINED_ERROR,
    CREDENTIALS_APP_ID_ERROR,
    KMS_KEY_ARN_ERROR,
]

# Credential inputs for the validation root. The ARNs are well formed but name nothing: the
# validation tests never reach an account.
TOKEN_SECRET_ARN = (
    "arn:aws:secretsmanager:us-west-2:123456789012:secret:GITHUB_TOKEN-AbCdEf"
)
PEM_SECRET_ARN = (
    "arn:aws:secretsmanager:us-west-2:123456789012:secret:github-app-pem-AbCdEf"
)
KMS_KEY_ARN = (
    "arn:aws:kms:us-west-2:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab"
)
# The validation root's credential unless a test passes its own.
DEFAULT_CREDENTIALS = {"github_token_secret_arn": "placeholder"}


def _terraform(args: List[str], cwd: str) -> subprocess.CompletedProcess:
    """
    Run terraform in ``cwd`` with no AWS credentials or configuration available.

    :param args: Terraform arguments, e.g. ``["plan", "-no-color"]``.
    :param cwd: Directory with the root module.
    :return: The completed process, with stdout and stderr captured as text.
    """
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("AWS_")
    }
    env.update(
        {
            "AWS_CONFIG_FILE": os.devnull,
            "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
            "AWS_EC2_METADATA_DISABLED": "true",
        }
    )
    return subprocess.run(
        ["terraform", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_validation_root(
    root_dir: str,
    arguments: Dict[str, Any],
    credentials: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Write a root module that calls this module with the given arguments as literals.

    The values are literals, not root variables, so that Terraform validates them
    before planning. The required inputs are placeholders that are never used.

    :param root_dir: Directory to write ``main.tf`` into.
    :param arguments: Module arguments to pass besides the required ones, by name.
        Each value is written as a JSON literal.
    :param credentials: The GitHub credential inputs, by name, written the same way.
        ``None`` passes ``DEFAULT_CREDENTIALS``, a token secret; ``{}`` passes none.
    """
    if credentials is None:
        credentials = DEFAULT_CREDENTIALS
    lines = [
        'module "actions-runner" {',
        f'  source                  = "{osp.relpath(REPO_ROOT, root_dir)}"',
        '  environment             = "development"',
        f'  github_org_name         = "{GITHUB_ORG_NAME}"',
        '  subnet_ids              = ["subnet-placeholder"]',
        '  alarm_emails            = ["placeholder@example.com"]',
    ]
    lines += [
        f"  {name} = {json.dumps(value)}"
        for name, value in {**credentials, **arguments}.items()
    ]
    lines.append("}")
    with open(osp.join(root_dir, "main.tf"), "w") as fp:
        fp.write("\n".join(lines) + "\n")


def _plan_validation_root(root_dir: str) -> str:
    """
    Run ``terraform plan`` on the validation root, which has no AWS account to reach.

    The plan always fails: without credentials, the AWS provider can't be configured. Terraform
    still evaluates the module's variable validations and the preconditions that don't depend on
    the AWS provider, so their errors are in the output.

    :param root_dir: Directory with the initialized validation root.
    :return: The plan's stdout and stderr, with whitespace collapsed, because Terraform wraps
        diagnostic messages.
    """
    result = _terraform(["plan", "-input=false", "-no-color"], root_dir)
    output = result.stdout + result.stderr
    LOG.info("terraform plan output:\n%s", output)
    assert result.returncode != 0, output
    return " ".join(output.split())


@pytest.fixture(scope="module")
def validation_root(tmp_path_factory) -> str:
    """
    Initialize a root module that calls this module, once for all validation tests.

    :return: Path to the initialized root module directory.
    """
    root_dir = str(tmp_path_factory.mktemp("variable-validation"))
    _write_validation_root(root_dir, {"python_version": DEFAULT_PYTHON_VERSION})
    result = _terraform(["init", "-input=false", "-no-color"], root_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    return root_dir


@pytest.mark.parametrize(
    "arguments, credentials, expected_errors",
    [
        # lambda-monitored 2.x only supports the Amazon Linux 2023 runtimes. It rejects
        # python3.11 too, so only PYTHON_VERSION_ERROR shows this module does.
        (
            {"python_version": "python3.11"},
            None,
            [
                "Invalid value for variable",
                "var.python_version is",
                PYTHON_VERSION_ERROR,
            ],
        ),
        # Issue #111: the instance role is named from the pool, and role_name is gone.
        # A caller who still passes it, as 5.0.0 callers do, must be told at plan.
        (
            {"role_name": "actions-runner-noble"},
            None,
            [
                "Unsupported argument",
                'An argument named "role_name" is not expected here.',
            ],
        ),
        # SEC-46: the Lambdas need exactly one way to authenticate to GitHub.
        ({}, {}, ["Resource precondition failed", CREDENTIALS_NONE_ERROR]),
        # A KMS pool's Lambdas must not be able to read a token or a PEM.
        (
            {},
            {
                "github_app_kms_key_arn": KMS_KEY_ARN,
                "github_app_id": GH_APP_ID,
                "github_token_secret_arn": TOKEN_SECRET_ARN,
            },
            ["Resource precondition failed", CREDENTIALS_KMS_COMBINED_ERROR],
        ),
        (
            {},
            {
                "github_app_kms_key_arn": KMS_KEY_ARN,
                "github_app_id": GH_APP_ID,
                "github_app_pem_secret_arn": PEM_SECRET_ARN,
            },
            ["Resource precondition failed", CREDENTIALS_KMS_COMBINED_ERROR],
        ),
        # The App JWT names the App, so App authentication needs its ID.
        (
            {},
            {"github_app_kms_key_arn": KMS_KEY_ARN},
            ["Resource precondition failed", CREDENTIALS_APP_ID_ERROR],
        ),
        (
            {},
            {"github_app_pem_secret_arn": PEM_SECRET_ARN},
            ["Resource precondition failed", CREDENTIALS_APP_ID_ERROR],
        ),
        # The Lambdas' kms:Sign grant is on the key ARN, which an alias ARN or a key ID
        # wouldn't match.
        (
            {},
            {
                "github_app_kms_key_arn": "arn:aws:kms:us-west-2:123456789012:alias/github-app",
                "github_app_id": GH_APP_ID,
            },
            [
                "Invalid value for variable",
                "var.github_app_kms_key_arn is",
                KMS_KEY_ARN_ERROR,
            ],
        ),
        (
            {},
            {
                "github_app_kms_key_arn": "1234abcd-12ab-34cd-56ef-1234567890ab",
                "github_app_id": GH_APP_ID,
            },
            [
                "Invalid value for variable",
                "var.github_app_kms_key_arn is",
                KMS_KEY_ARN_ERROR,
            ],
        ),
    ],
    ids=[
        "python-3.11",
        "role-name-removed",
        "no-credentials",
        "kms-with-token",
        "kms-with-pem",
        "kms-without-app-id",
        "pem-without-app-id",
        "kms-alias-arn",
        "kms-key-id",
    ],
)
def test_invalid_input_fails_at_plan(
    validation_root, arguments, credentials, expected_errors
):
    """An argument this module rejects fails at plan, with this module's own error."""
    _write_validation_root(validation_root, arguments, credentials)
    flat_output = _plan_validation_root(validation_root)

    for expected_error in expected_errors:
        assert expected_error in flat_output, flat_output
    # Only the rule under test fires, so each case would fail without its own rule.
    for other_error in MODULE_INPUT_ERRORS:
        if other_error not in expected_errors:
            assert other_error not in flat_output, flat_output


@pytest.mark.parametrize(
    "arguments, credentials",
    [
        ({}, None),
        ({"python_version": "python3.13"}, None),
        ({}, {"github_token_secret_arn": TOKEN_SECRET_ARN}),
        (
            {},
            {"github_app_pem_secret_arn": PEM_SECRET_ARN, "github_app_id": GH_APP_ID},
        ),
        ({}, {"github_app_kms_key_arn": KMS_KEY_ARN, "github_app_id": GH_APP_ID}),
        # Multi-Region keys have IDs of another form.
        (
            {},
            {
                "github_app_kms_key_arn": "arn:aws:kms:us-west-2:123456789012:key/"
                "mrk-1234abcd12ab34cd56ef1234567890ab",
                "github_app_id": GH_APP_ID,
            },
        ),
        # A token and a PEM together worked before SEC-46, with the token in use, so they
        # still do, even without github_app_id.
        (
            {},
            {
                "github_token_secret_arn": TOKEN_SECRET_ARN,
                "github_app_pem_secret_arn": PEM_SECRET_ARN,
            },
        ),
    ],
    ids=[
        "defaults",
        "python-3.13",
        "token",
        "pem",
        "kms",
        "kms-multi-region-key",
        "token-with-pem",
    ],
)
def test_valid_input_passes_validation(validation_root, arguments, credentials):
    """The defaults, every runtime and every credential combination this module supports are not rejected."""
    _write_validation_root(validation_root, arguments, credentials)
    result = _terraform(["validate", "-no-color"], validation_root)
    assert result.returncode == 0, result.stdout + result.stderr

    # terraform validate doesn't evaluate preconditions, so the plan has to show that no
    # rule rejects the input. It fails only because there are no AWS credentials.
    flat_output = _plan_validation_root(validation_root)
    assert "Resource precondition failed" not in flat_output, flat_output
    assert "Invalid value for variable" not in flat_output, flat_output
    for module_error in MODULE_INPUT_ERRORS:
        assert module_error not in flat_output, flat_output


# The examples are validated the same way, with no AWS account, against this checkout rather
# than the published module: an example that only works with the release it pins would hide
# a change on this branch that breaks it.
EXAMPLES_DIR = osp.join(REPO_ROOT, "examples")
EXAMPLES = sorted(
    name for name in os.listdir(EXAMPLES_DIR) if osp.isdir(osp.join(EXAMPLES_DIR, name))
)
# An example calls the published module as a reader would copy it: the registry source, and
# an exact version on the next line.
REGISTRY_MODULE_RE = re.compile(
    r"^(?P<indent>[ \t]*)source[ \t]*=[ \t]*"
    r'"registry\.infrahouse\.com/infrahouse/actions-runner/aws"[ \t]*\n'
    r'[ \t]*version[ \t]*=[ \t]*"(?P<version>[^"]*)"[ \t]*\n',
    re.MULTILINE,
)


def _current_version() -> str:
    """
    Return the module's current version, the one a release bumps.

    :return: ``current_version`` from ``.bumpversion.cfg``.
    """
    config = configparser.ConfigParser(interpolation=None)
    config.read(osp.join(REPO_ROOT, ".bumpversion.cfg"))
    return config["bumpversion"]["current_version"]


def _point_example_at_checkout(root_dir: str) -> str:
    """
    Make the example in ``root_dir`` call this checkout instead of the registry module.

    ``main.tf`` is rewritten in place: the registry source and its version become a local source.

    :param root_dir: Directory with a copy of the example.
    :return: The version the example pins.
    :raises ValueError: If ``main.tf`` doesn't call the registry module exactly once, with its
        version on the line after the source. Otherwise the test could validate another module.
    """
    main_tf = osp.join(root_dir, "main.tf")
    with open(main_tf) as fp:
        content = fp.read()
    matches = list(REGISTRY_MODULE_RE.finditer(content))
    if len(matches) != 1:
        raise ValueError(
            f"{main_tf} must call registry.infrahouse.com/infrahouse/actions-runner/aws once, "
            f"with its version on the next line. Found {len(matches)} such calls."
        )
    match = matches[0]
    local_source = f'{match["indent"]}source = "{osp.relpath(REPO_ROOT, root_dir)}"\n'
    with open(main_tf, "w") as fp:
        fp.write(content[: match.start()] + local_source + content[match.end() :])
    return match["version"]


@pytest.mark.parametrize("example", EXAMPLES)
def test_example_validates(example: str, tmp_path: Path) -> None:
    """An example pins the current version and passes init and validate against this checkout."""
    root_dir = str(tmp_path / example)
    copytree(
        osp.join(EXAMPLES_DIR, example),
        root_dir,
        ignore=ignore_patterns(
            ".terraform", ".terraform.lock.hcl", "terraform.tfstate*"
        ),
    )
    pinned_version = _point_example_at_checkout(root_dir)
    assert pinned_version == _current_version(), (
        f"examples/{example} pins version {pinned_version}, but the module is at "
        f"{_current_version()}. Add examples/{example}/main.tf to .bumpversion.cfg."
    )

    for args in (
        ["init", "-backend=false", "-input=false", "-no-color"],
        ["validate", "-no-color"],
    ):
        result = _terraform(args, root_dir)
        assert result.returncode == 0, result.stdout + result.stderr


def test_example_without_registry_source_is_rejected(tmp_path: Path) -> None:
    """An example that doesn't call the registry module fails instead of being validated."""
    (tmp_path / "main.tf").write_text(
        'module "actions_runner" {\n  source = "../.."\n}\n'
    )
    with pytest.raises(ValueError, match="Found 0 such calls"):
        _point_example_at_checkout(str(tmp_path))
