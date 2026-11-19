import json
import os
import platform
import subprocess
import sys
from os import path as osp, remove
from shutil import rmtree
from textwrap import dedent
from typing import List

import boto3
import pytest
from infrahouse_core.github import GitHubActions, GitHubAuth
from pytest_infrahouse import terraform_apply

from tests.conftest import (
    LOG,
    TERRAFORM_ROOT_DIR,
    GITHUB_ORG_NAME,
    assert_lambda_memory_within_limit,
    assert_no_inspector_exclusion_tag,
    assert_role_name_prefixes,
    ensure_runners,
    get_tmp_token,
    invoke_deregistration_sweep,
    GH_APP_ID,
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
            # pattern without also selecting the instance role.
            assert_role_name_prefixes(
                boto3_session,
                aws_region,
                tf_output["runner_role_arn"]["value"],
                {
                    "reg": tf_output["registration_lambda_name"]["value"],
                    "dereg": tf_output["deregistration_lambda_name"]["value"],
                    "metric": tf_output["record_metric_lambda_name"]["value"],
                },
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
            cloudwatch_client = boto3_session.client(
                "cloudwatch", region_name=aws_region
            )
            deregistration_lambda_name = tf_output["deregistration_lambda_name"][
                "value"
            ]
            invoke_deregistration_sweep(lambda_client, deregistration_lambda_name)

            for lambda_name in (
                tf_output["registration_lambda_name"]["value"],
                deregistration_lambda_name,
                tf_output["record_metric_lambda_name"]["value"],
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
DEFAULT_ROLE_NAME = "actions-runner"
DEFAULT_PYTHON_VERSION = "python3.12"


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


def _write_validation_root(root_dir: str, role_name: str, python_version: str) -> None:
    """
    Write a root module that calls this module with the given inputs as literals.

    The values are literals, not root variables, so that Terraform validates them
    before planning. The other required inputs are placeholders that are never used.

    :param root_dir: Directory to write ``main.tf`` into.
    :param role_name: Value for the module's ``role_name`` input.
    :param python_version: Value for the module's ``python_version`` input.
    """
    with open(osp.join(root_dir, "main.tf"), "w") as fp:
        fp.write(dedent(f"""
                module "actions-runner" {{
                  source                  = "{osp.relpath(REPO_ROOT, root_dir)}"
                  environment             = "development"
                  github_org_name         = "{GITHUB_ORG_NAME}"
                  github_token_secret_arn = "placeholder"
                  subnet_ids              = ["subnet-placeholder"]
                  alarm_emails            = ["placeholder@example.com"]
                  role_name               = {json.dumps(role_name)}
                  python_version          = {json.dumps(python_version)}
                }}
                """))


@pytest.fixture(scope="module")
def validation_root(tmp_path_factory) -> str:
    """
    Initialize a root module that calls this module, once for all validation tests.

    :return: Path to the initialized root module directory.
    """
    root_dir = str(tmp_path_factory.mktemp("variable-validation"))
    _write_validation_root(root_dir, DEFAULT_ROLE_NAME, DEFAULT_PYTHON_VERSION)
    result = _terraform(["init", "-input=false", "-no-color"], root_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    return root_dir


@pytest.mark.parametrize(
    "role_name, python_version, invalid_variable",
    [
        # actions-runner-instance- is 24 characters, so 41 makes a 65-character role name.
        ("r" * 41, DEFAULT_PYTHON_VERSION, "role_name"),
        ("actions/runner", DEFAULT_PYTHON_VERSION, "role_name"),
        ("actions-runner*", DEFAULT_PYTHON_VERSION, "role_name"),
        ("", DEFAULT_PYTHON_VERSION, "role_name"),
        # lambda-monitored 2.x only supports the Amazon Linux 2023 runtimes.
        (DEFAULT_ROLE_NAME, "python3.11", "python_version"),
    ],
    ids=[
        "role-name-too-long",
        "role-name-slash",
        "role-name-wildcard",
        "role-name-empty",
        "python-3.11",
    ],
)
def test_invalid_input_fails_at_plan(
    validation_root, role_name, python_version, invalid_variable
):
    """An input that would produce an invalid role name or runtime fails at plan."""
    _write_validation_root(validation_root, role_name, python_version)
    result = _terraform(["plan", "-input=false", "-no-color"], validation_root)
    output = result.stdout + result.stderr
    LOG.info("terraform plan output:\n%s", output)

    assert result.returncode != 0, output
    assert "Invalid value for variable" in output, output
    assert f"var.{invalid_variable} is" in output, output


@pytest.mark.parametrize(
    "role_name, python_version",
    [
        # The longest role_name that keeps actions-runner-instance-<role_name> within 64.
        ("r" * 40, DEFAULT_PYTHON_VERSION),
        ("Runner_1+=,.@-x", "python3.13"),
    ],
    ids=["role-name-40-chars", "role-name-all-iam-characters"],
)
def test_valid_input_passes_validation(validation_root, role_name, python_version):
    """Inputs at the edge of what IAM and Lambda accept are not rejected."""
    _write_validation_root(validation_root, role_name, python_version)
    result = _terraform(["validate", "-no-color"], validation_root)
    assert result.returncode == 0, result.stdout + result.stderr
