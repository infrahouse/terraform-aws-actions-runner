"""
Test migration from version 3.0.3 to the current version with lambda-monitored module.

This test ensures that users can smoothly upgrade from v3.0.3 (which uses custom lambda setup)
to the new version (which uses terraform-aws-lambda-monitored module) without destroying
and recreating resources.
"""

import json
import platform
import sys
import time
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
    ensure_runners,
    get_tmp_token,
    GH_APP_ID,
)


def write_main_tf(terraform_module_dir, source, version=None):
    """
    Generate main.tf for the test with specified source and version.

    Args:
        terraform_module_dir: Directory where main.tf should be written
        source: Terraform module source (registry or local path)
        version: Module version (only used with registry source)
    """
    version_line = f'  version = "{version}"\n' if version else ""

    with open(osp.join(terraform_module_dir, "main.tf"), "w") as fp:
        fp.write(
            dedent(
                f"""
                module "actions-runner" {{
                  source  = "{source}"
                  {version_line}
                  instance_type             = "t3a.small"
                  asg_min_size              = 1
                  asg_max_size              = var.asg_max_size
                  subnet_ids                = var.subnet_ids
                  lambda_subnet_ids         = var.lambda_subnet_ids
                  environment               = local.environment
                  github_org_name           = var.github_org_name
                  github_app_pem_secret_arn = var.github_app_pem_secret_arn
                  github_token_secret_arn   = var.github_token != null ? aws_secretsmanager_secret.github_token.arn : null
                  puppet_hiera_config_path  = "/opt/infrahouse-puppet-data/environments/${{local.environment}}/hiera.yaml"
                  packages = [
                    "infrahouse-puppet-data"
                  ]
                  extra_labels    = ["awesome"]
                  github_app_id   = var.github_app_id
                  ubuntu_codename = var.ubuntu_codename
                  architecture    = var.architecture
                  python_version  = var.python_version
                  alarm_emails = [
                    "aleks+terraform-aws-actions-runner@infrahouse.com"
                  ]
                }}
                """
            )
        )


@pytest.mark.parametrize("aws_provider_version", ["~> 5.62"], ids=["aws-5"])
@pytest.mark.parametrize(
    "secret_type, ubuntu_codename",
    [
        ("token", "noble"),
    ],
)
def test_module_migration(
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
    """
    Test migration from v3.0.3 to current version.

    1. Deploy v3.0.3 (old version without lambda-monitored)
    2. Verify log group exists
    3. Write test message to log group
    4. Upgrade to current version (with lambda-monitored and moved blocks)
    5. Verify migration is smooth (no destroy/recreate)
    6. Verify log group still exists with same ARN
    7. Verify test message persists (data was preserved)
    """
    subnet_public_ids = service_network["subnet_public_ids"]["value"]
    subnet_private_ids = service_network["subnet_private_ids"]["value"]

    if secret_type == "token":
        assert github_token, "Set GitHub token value with --github-token CLI argument."
    elif secret_type == "pem":
        assert (
            github_app_pem_secret_arn
        ), "Set a secret ARN containing GitHub APP PEM key token value with --github-app-pem-secret CLI argument."

    terraform_module_dir = osp.join(TERRAFORM_ROOT_DIR, "actions-runner-3")

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
        fp.write(
            dedent(
                f"""
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
                """
            )
        )

    with open(osp.join(terraform_module_dir, "terraform.tfvars"), "w") as fp:
        asg_max_size = 2
        fp.write(
            dedent(
                f"""
                    region          = "{aws_region}"
                    github_org_name = "{GITHUB_ORG_NAME}"
                    ubuntu_codename = "{ubuntu_codename}"

                    subnet_ids         = {json.dumps(subnet_public_ids)}
                    lambda_subnet_ids  = {json.dumps(subnet_private_ids)}
                    architecture       = "{platform.machine()}"
                    python_version     = "python{sys.version_info.major}.{sys.version_info.minor}"
                    asg_max_size = {asg_max_size}
                    """
            )
        )
        if test_role_arn:
            fp.write(
                dedent(
                    f"""
                    role_arn        = "{test_role_arn}"
                    """
                )
            )

        fp.write(
            f'github_token = "{github_token}"'
            if secret_type == "token"
            else dedent(
                f"""
                github_app_pem_secret_arn = "{github_app_pem_secret_arn}"
                github_app_id = {GH_APP_ID}
                """
            )
        )

    # PHASE 1: Deploy old version (3.0.3)
    LOG.info("=" * 80)
    LOG.info("PHASE 1: Deploying version 3.0.3 (old version)")
    LOG.info("=" * 80)

    # Generate main.tf for Phase 1 with registry source
    write_main_tf(
        terraform_module_dir,
        source="registry.infrahouse.com/infrahouse/actions-runner/aws",
        version="3.0.3",
    )
    LOG.info("Generated main.tf for v3.0.3 from registry")

    with terraform_apply(
        terraform_module_dir,
        destroy_after=False,  # Don't destroy - we need to test migration
        json_output=True,
    ) as tf_output:
        LOG.info("Phase 1 output: %s", json.dumps(tf_output, indent=4))

        # Construct log group name from ASG name (v3.0.3 pattern)
        asg_name = tf_output["autoscaling_group_name"]["value"]
        log_group_name = f"/aws/lambda/{asg_name}_deregistration"
        LOG.info(f"Deregistration log group (constructed): {log_group_name}")

        logs_client = boto3_session.client("logs", region_name=aws_region)

        # Check log group exists
        response = logs_client.describe_log_groups(logGroupNamePrefix=log_group_name)
        assert (
            len(response["logGroups"]) == 1
        ), f"Log group {log_group_name} should exist"
        old_log_group = response["logGroups"][0]
        old_log_group_arn = old_log_group["arn"]
        LOG.info(f"✓ Log group exists: {log_group_name} (ARN: {old_log_group_arn})")

        # Write a test message to the log group to verify data persists through migration
        test_log_stream = "migration-test-stream"
        test_message = f"TEST_MIGRATION_MARKER_{int(time.time())}"

        try:
            logs_client.create_log_stream(
                logGroupName=log_group_name, logStreamName=test_log_stream
            )
            LOG.info(f"Created test log stream: {test_log_stream}")
        except logs_client.exceptions.ResourceAlreadyExistsException:
            LOG.info(f"Test log stream already exists: {test_log_stream}")

        # Put test log event
        logs_client.put_log_events(
            logGroupName=log_group_name,
            logStreamName=test_log_stream,
            logEvents=[{"timestamp": int(time.time() * 1000), "message": test_message}],
        )
        LOG.info(f"✓ Wrote test message to log group: {test_message}")

    # PHASE 2: Upgrade to current version (with lambda-monitored)
    LOG.info("=" * 80)
    LOG.info("PHASE 2: Upgrading to current version (with lambda-monitored)")
    LOG.info("=" * 80)

    # Generate main.tf for Phase 2 with local source
    write_main_tf(terraform_module_dir, source="../..")
    LOG.info("Generated main.tf with local source")

    # Apply the migration
    with terraform_apply(
        terraform_module_dir,
        destroy_after=not keep_after,
        json_output=True,
    ) as migrated_output:
        LOG.info("Phase 2 output: %s", json.dumps(migrated_output, indent=4))

        # Verify the log group still exists and wasn't recreated
        response = logs_client.describe_log_groups(logGroupNamePrefix=log_group_name)
        assert (
            len(response["logGroups"]) == 1
        ), f"Log group {log_group_name} should still exist after migration"
        new_log_group = response["logGroups"][0]
        new_log_group_arn = new_log_group["arn"]

        # Verify it's the SAME log group (same ARN) - this proves it wasn't destroyed/recreated
        assert (
            new_log_group_arn == old_log_group_arn
        ), f"Log group should not be recreated. Old ARN: {old_log_group_arn}, New ARN: {new_log_group_arn}"

        LOG.info(f"✓ Log group survived migration: {log_group_name}")

        # Verify the test message still exists in the log group (proves data was preserved)
        log_events_response = logs_client.get_log_events(
            logGroupName=log_group_name,
            logStreamName=test_log_stream,
            startFromHead=True,
        )

        messages = [event["message"] for event in log_events_response["events"]]
        assert test_message in messages, (
            f"Test message '{test_message}' not found in log stream. "
            f"Messages found: {messages}. This indicates log data was lost during migration!"
        )

        LOG.info(f"✓ Test message survived migration: {test_message}")
        LOG.info(
            "✓ Migration successful - resources were moved, not destroyed/recreated, and data was preserved"
        )

        # Verify runners work
        gha = GitHubActions(GitHubAuth(github_token, GITHUB_ORG_NAME))
        try:
            autoscaling_client = boto3_session.client(
                "autoscaling", region_name=aws_region
            )
            ensure_runners(
                gha,
                aws_region,
                autoscaling_client,
                timeout_time=900 + asg_max_size * 900,
                test_role_arn=test_role_arn,
            )
        finally:
            if not keep_after:
                # Cleanup
                registration_token_secret_prefix = migrated_output[
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
