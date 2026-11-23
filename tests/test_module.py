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
from typing import Dict, List

import boto3
import pytest
from infrahouse_core.github import GitHubActions, GitHubAuth
from pytest_infrahouse import terraform_apply

from tests.conftest import (
    LOG,
    TERRAFORM_ROOT_DIR,
    GITHUB_ORG_NAME,
    assert_error_rate_alarm,
    assert_immediate_error_alarm,
    assert_lambda_memory_within_limit,
    assert_no_inspector_exclusion_tag,
    assert_role_name_prefixes,
    assert_role_outputs,
    ensure_runners,
    get_tmp_token,
    invoke_deregistration_sweep,
    DEREGISTRATION_ERROR_RATE_PERIOD,
    GH_APP_ID,
    RECORD_METRIC_ERROR_RATE_PERIOD,
)


@pytest.mark.parametrize("aws_provider_version", ["~> 6.0"], ids=["aws-6"])
@pytest.mark.parametrize(
    "secret_type, ubuntu_codename",
    [
        ("token", "noble"),
    ],
)
def test_module(
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

    if secret_type == "token":
        assert github_token, "Set GitHub token value with --github-token CLI argument."
    elif secret_type == "pem":
        assert (
            github_app_pem_secret_arn
        ), "Set a secret ARN containing GitHub APP PEM key token value with --github-app-pem-secret CLI argument."

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

        fp.write(
            f'github_token = "{github_token}"'
            if secret_type == "token"
            else dedent(f"""
                github_app_pem_secret_arn = "{github_app_pem_secret_arn}"
                github_app_id = {GH_APP_ID}
                """)
        )

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

            for lambda_name in (
                registration_lambda_name,
                deregistration_lambda_name,
                record_metric_lambda_name,
            ):
                assert_lambda_memory_within_limit(cloudwatch_client, lambda_name)
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


def _write_validation_root(root_dir: str, arguments: Dict[str, str]) -> None:
    """
    Write a root module that calls this module with the given arguments as literals.

    The values are literals, not root variables, so that Terraform validates them
    before planning. The required inputs are placeholders that are never used.

    :param root_dir: Directory to write ``main.tf`` into.
    :param arguments: Module arguments to pass besides the required ones, by name.
        Each value is written as a JSON literal.
    """
    lines = [
        'module "actions-runner" {',
        f'  source                  = "{osp.relpath(REPO_ROOT, root_dir)}"',
        '  environment             = "development"',
        f'  github_org_name         = "{GITHUB_ORG_NAME}"',
        '  github_token_secret_arn = "placeholder"',
        '  subnet_ids              = ["subnet-placeholder"]',
        '  alarm_emails            = ["placeholder@example.com"]',
    ]
    lines += [f"  {name} = {json.dumps(value)}" for name, value in arguments.items()]
    lines.append("}")
    with open(osp.join(root_dir, "main.tf"), "w") as fp:
        fp.write("\n".join(lines) + "\n")


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
    "arguments, expected_errors",
    [
        # lambda-monitored 2.x only supports the Amazon Linux 2023 runtimes. It rejects
        # python3.11 too, so only PYTHON_VERSION_ERROR shows this module does.
        (
            {"python_version": "python3.11"},
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
            [
                "Unsupported argument",
                'An argument named "role_name" is not expected here.',
            ],
        ),
    ],
    ids=["python-3.11", "role-name-removed"],
)
def test_invalid_input_fails_at_plan(validation_root, arguments, expected_errors):
    """An argument this module rejects fails at plan, with this module's own error."""
    _write_validation_root(validation_root, arguments)
    result = _terraform(["plan", "-input=false", "-no-color"], validation_root)
    output = result.stdout + result.stderr
    LOG.info("terraform plan output:\n%s", output)
    # Terraform wraps diagnostic messages, so compare with whitespace collapsed.
    flat_output = " ".join(output.split())

    assert result.returncode != 0, output
    for expected_error in expected_errors:
        assert expected_error in flat_output, output


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"python_version": "python3.13"},
    ],
    ids=["defaults", "python-3.13"],
)
def test_valid_input_passes_validation(validation_root, arguments):
    """The defaults and every runtime this module supports are not rejected."""
    _write_validation_root(validation_root, arguments)
    result = _terraform(["validate", "-no-color"], validation_root)
    assert result.returncode == 0, result.stdout + result.stderr


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
