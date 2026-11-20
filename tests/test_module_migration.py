"""
Test upgrades from earlier releases to the current version.

- From 3.0.3 (custom Lambda setup) to the terraform-aws-lambda-monitored based version: the moved
  blocks must carry the deregistration log group over instead of destroying and recreating it.
  Skipped: 3.0.3 no longer bootstraps a runner (see the skip reason).
- From 4.4.1, the last release before issue #109: the Lambda and instance roles are replaced with
  ones carrying the new name prefixes, and the runners must roll, so that no runner keeps
  credentials of the deleted instance role.
- From 5.0.0, the last release with ``role_name`` (issue #111): the instance role
  ``actions-runner-instance-<role_name>`` is replaced with ``actions-runner-instance-<pool>``, and
  the runners must roll for the same reason.

Both releases take ``role_name``, so phase 1 sets it, as callers who run more than one pool do.
Phase 2 doesn't: the current version has no such input.
"""

import json
import platform
import sys
import time
from os import path as osp, remove
from secrets import token_hex
from shutil import rmtree
from textwrap import dedent
from typing import Dict, Optional, Set

import boto3
import pytest
from infrahouse_core.github import GitHubActions, GitHubAuth
from pytest_infrahouse import terraform_apply

from tests.conftest import (
    LOG,
    TERRAFORM_ROOT_DIR,
    GITHUB_ORG_NAME,
    assert_role_name_prefixes,
    ensure_runners,
    get_tmp_token,
    GH_APP_ID,
)


def in_service_instance_ids(autoscaling_client, asg_name: str) -> Set[str]:
    """
    Return the ids of the ASG's instances that are in service.

    Instances in a ``Terminating*`` state are on their way out and are not counted.

    :param autoscaling_client: Boto3 Auto Scaling client.
    :param asg_name: Auto Scaling group name.
    :return: Instance ids whose lifecycle state is ``InService``.
    """
    response = autoscaling_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )
    return {
        instance["InstanceId"]
        for group in response["AutoScalingGroups"]
        for instance in group["Instances"]
        if instance["LifecycleState"] == "InService"
    }


def instance_profile_of_role(iam_client, role_name: str) -> Dict:
    """
    Return the one instance profile that holds a role.

    :param iam_client: Boto3 IAM client.
    :param role_name: Name of the role.
    :return: The instance profile, as ``ListInstanceProfilesForRole`` describes it.
    :raises AssertionError: If the role isn't in exactly one instance profile.
    """
    profiles = iam_client.list_instance_profiles_for_role(RoleName=role_name)[
        "InstanceProfiles"
    ]
    assert len(profiles) == 1, f"Role {role_name} is in instance profiles {profiles}"
    return profiles[0]


def write_main_tf(
    terraform_module_dir: str,
    source: str,
    version: Optional[str] = None,
    role_name: Optional[str] = None,
) -> None:
    """
    Generate main.tf for the test with specified source and version.

    :param terraform_module_dir: Directory where main.tf should be written.
    :param source: Terraform module source (registry or local path).
    :param version: Module version (only used with registry source).
    :param role_name: Value for the module's ``role_name`` input, which releases before
        6.0.0 take. ``None`` leaves the argument out.
    """
    version_line = f'  version = "{version}"\n' if version else ""
    role_name_line = f"  role_name = {json.dumps(role_name)}\n" if role_name else ""

    with open(osp.join(terraform_module_dir, "main.tf"), "w") as fp:
        fp.write(dedent(f"""
                module "actions-runner" {{
                  source  = "{source}"
                  {version_line}
                  {role_name_line}
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
                """))


@pytest.mark.parametrize(
    "from_version",
    [
        pytest.param(
            "3.0.3",
            id="from-3.0.3",
            marks=pytest.mark.skip(
                reason="3.0.3 can't bootstrap a runner any more: its cloud-init 2.2.2 checks the InfraHouse "
                "noble apt key against a hard-coded fingerprint, and that key has been rotated. The repository "
                "isn't added, so puppet-code and infrahouse-toolkit don't install and the bootstrap hook "
                "times out."
            ),
        ),
        pytest.param("4.4.1", id="from-4.4.1"),
        pytest.param("5.0.0", id="from-5.0.0"),
    ],
)
@pytest.mark.parametrize("aws_provider_version", ["~> 6.0"], ids=["aws-6"])
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
    from_version,
):
    """
    Test the upgrade from ``from_version`` to the current version.

    1. Deploy ``from_version`` with a ``role_name`` that starts with ``actions-runner-``
    2. Verify the deregistration log group exists and write a test message to it
    3. Upgrade to the current version, without ``role_name``
    4. Verify the log group has the same ARN and still holds the test message
    5. Verify the runners come back online after the instance refresh
    6. Verify no instance from step 1 is still in service: the instance role was replaced,
       and runners launched with the old role hold credentials of a deleted role
    7. Verify the instance role AWS reports is ``actions-runner-instance-<pool>`` and the
       Lambda roles carry the same ``<pool>``, the one the pool had before the upgrade
    8. Verify the instance profile from step 1 is the same profile and now holds the new role
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

    # PHASE 1: Deploy the old version
    LOG.info("=" * 80)
    LOG.info("PHASE 1: Deploying version %s (old version)", from_version)
    LOG.info("=" * 80)

    # Generate main.tf for Phase 1 with registry source. Callers who set role_name
    # usually started it with actions-runner-, which 5.0.0 turned into
    # actions-runner-instance-actions-runner-<codename> (issue #111). The random part
    # keeps the role clear of others in the shared test account: 4.x uses role_name
    # verbatim, and a role named actions-runner-noble may already exist there.
    write_main_tf(
        terraform_module_dir,
        source="registry.infrahouse.com/infrahouse/actions-runner/aws",
        version=from_version,
        role_name=f"actions-runner-{ubuntu_codename}-{token_hex(3)}",
    )
    LOG.info("Generated main.tf for %s from registry", from_version)

    with terraform_apply(
        terraform_module_dir,
        destroy_after=False,  # Don't destroy - we need to test migration
        json_output=True,
    ) as tf_output:
        LOG.info("Phase 1 output: %s", json.dumps(tf_output, indent=4))

        # Construct log group name from ASG name (v3.0.3 pattern)
        asg_name = tf_output["autoscaling_group_name"]["value"]

        autoscaling_client = boto3_session.client("autoscaling", region_name=aws_region)
        old_instance_ids = in_service_instance_ids(autoscaling_client, asg_name)
        LOG.info("Instances in service before the upgrade: %s", old_instance_ids)
        assert old_instance_ids, f"{from_version} has no instances in service"

        iam_client = boto3_session.client("iam")
        old_role_name = tf_output["runner_role_arn"]["value"].split("/")[-1]
        old_instance_profile = instance_profile_of_role(iam_client, old_role_name)
        LOG.info(
            "Instance role before the upgrade: %s, in instance profile %s (%s)",
            old_role_name,
            old_instance_profile["InstanceProfileName"],
            old_instance_profile["InstanceProfileId"],
        )

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

    # PHASE 2: Upgrade to the current version
    LOG.info("=" * 80)
    LOG.info("PHASE 2: Upgrading from %s to the current version", from_version)
    LOG.info("=" * 80)

    # Generate main.tf for Phase 2 with local source. The current version has no
    # role_name input, so a caller upgrading deletes it from the module block.
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
            ensure_runners(
                gha,
                aws_region,
                autoscaling_client,
                timeout_time=900 + asg_max_size * 900,
                test_role_arn=test_role_arn,
            )

            # ensure_runners waited for the instance refresh. A runner launched before
            # the upgrade holds credentials of the instance role that was just deleted.
            new_instance_ids = in_service_instance_ids(autoscaling_client, asg_name)
            LOG.info("Instances in service after the upgrade: %s", new_instance_ids)
            assert not old_instance_ids & new_instance_ids, (
                f"Instances {sorted(old_instance_ids & new_instance_ids)} were launched by "
                f"{from_version} with the old instance role and are still in service: "
                "the upgrade did not roll the runners."
            )

            # asg_name is the pool's name before the upgrade, so this also proves the
            # upgrade kept <pool>, and that the instance role is named from it.
            new_role_arn = migrated_output["runner_role_arn"]["value"]
            assert_role_name_prefixes(
                boto3_session,
                aws_region,
                new_role_arn,
                asg_name,
                {
                    "reg": f"{asg_name}_registration",
                    "dereg": f"{asg_name}_deregistration",
                    "metric": f"{asg_name}_record_metric",
                },
            )

            # The instance profile keeps its name; only its role changes. A replaced
            # profile would get a new unique id even under the same name.
            new_role_name = new_role_arn.split("/")[-1]
            new_instance_profile = instance_profile_of_role(iam_client, new_role_name)
            LOG.info(
                "Instance role after the upgrade: %s, in instance profile %s (%s)",
                new_role_name,
                new_instance_profile["InstanceProfileName"],
                new_instance_profile["InstanceProfileId"],
            )
            assert (
                new_role_name != old_role_name
            ), f"The upgrade from {from_version} kept the instance role {old_role_name}"
            assert (
                new_instance_profile["InstanceProfileId"]
                == old_instance_profile["InstanceProfileId"]
            ), (
                f"Instance profile {old_instance_profile['Arn']} was replaced by "
                f"{new_instance_profile['Arn']}; it should only have changed its role"
            )
            assert [role["RoleName"] for role in new_instance_profile["Roles"]] == [
                new_role_name
            ], f"Instance profile {new_instance_profile['Arn']} holds {new_instance_profile['Roles']}"
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
