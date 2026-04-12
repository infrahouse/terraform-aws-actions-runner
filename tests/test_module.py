import json
import platform
import sys
from os import path as osp, remove
from shutil import rmtree
from textwrap import dedent

import boto3
import pytest
from infrahouse_core.github import GitHubActions, GitHubAuth
from pytest_infrahouse import terraform_apply

from tests.conftest import (
    LOG,
    TERRAFORM_ROOT_DIR,
    GITHUB_ORG_NAME,
    assert_lambda_memory_within_limit,
    ensure_runners,
    get_tmp_token,
    invoke_deregistration_sweep,
    GH_APP_ID,
)


@pytest.mark.parametrize(
    "aws_provider_version", ["~> 6.0"], ids=["aws-6"]
)
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
