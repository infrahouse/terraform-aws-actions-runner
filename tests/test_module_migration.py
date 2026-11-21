"""
Test upgrades from earlier releases to the current version.

- From 3.0.3 (custom Lambda setup) to the terraform-aws-lambda-monitored based version: the moved
  blocks must carry the deregistration log group over instead of destroying and recreating it.
  Skipped: 3.0.3 no longer bootstraps a runner (see the skip reason).
- From 4.4.1, the last release before issue #109: the Lambda and instance roles are replaced with
  ones carrying the new name prefixes, and the runners must roll, so that no runner keeps
  credentials of the deleted instance role.
- From 5.0.0, the first release with those prefixes: nothing in the launch template changes, so the
  test also changes the root volume size to give the instance refresh something to replace.

Each upgrade is applied to a settled pool while every runner in service is protected from
scale-in, as the runner that runs a CD job is (``gha_prerun.sh``). The protection is lifted after
the apply, as ``gha_postrun.sh`` does when the job ends: once the refresh has ended, or after
half an hour if it hasn't. The instance refresh must wait for it and then replace those runners,
not skip them and report success (issue #112).
"""

import json
import platform
import sys
import time
from os import path as osp, remove
from shutil import rmtree
from textwrap import dedent
from typing import Optional, Set

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

# How long after the upgrade's apply the runners stay protected from scale-in at most: the rest
# of the CD job that ran the apply, which can outlast the refresh. Long enough for a refresh that
# ignores protected runners to replace the warm pool and end, and well under the hour that a
# refresh with ScaleInProtectedInstances=Wait waits for the protection to be lifted.
PROTECTION_HOLD_SECONDS = 1800

# How long the pool may take to settle before the upgrade: a warm pool instance bootstraps
# before it hibernates, and the bootstrap lifecycle hook times out after 20 minutes.
SETTLE_TIMEOUT_SECONDS = 1800

# How often to look at the pool and the refresh while waiting.
POLL_SECONDS = 30


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


def set_scale_in_protection(
    autoscaling_client, asg_name: str, instance_ids: Set[str], protected: bool
) -> None:
    """
    Protect instances from scale-in, or lift the protection.

    ``gha_prerun.sh`` protects a runner when a job starts, and ``gha_postrun.sh`` lifts the
    protection when the job ends.

    :param autoscaling_client: Boto3 Auto Scaling client.
    :param asg_name: Auto Scaling group name.
    :param instance_ids: Instances to change. They must be in service.
    :param protected: True to protect the instances, False to lift the protection.
    """
    LOG.info(
        "%s scale-in protection on %s",
        "Setting" if protected else "Lifting",
        sorted(instance_ids),
    )
    if instance_ids:
        autoscaling_client.set_instance_protection(
            InstanceIds=sorted(instance_ids),
            AutoScalingGroupName=asg_name,
            ProtectedFromScaleIn=protected,
        )


def log_instance_refresh(autoscaling_client, asg_name: str, when: str) -> None:
    """
    Log the ASG's most recent instance refresh: status, reason, progress, times and preferences.

    :param autoscaling_client: Boto3 Auto Scaling client.
    :param asg_name: Auto Scaling group name.
    :param when: The point in the test, for the log message.
    """
    refreshes = autoscaling_client.describe_instance_refreshes(
        AutoScalingGroupName=asg_name, MaxRecords=1
    )["InstanceRefreshes"]
    if not refreshes:
        LOG.info("No instance refresh of %s %s", asg_name, when)
        return
    refresh = refreshes[0]
    LOG.info(
        "Instance refresh %s %s: Status %s, StatusReason %r, PercentageComplete %s, "
        "StartTime %s, EndTime %s, Preferences %s",
        refresh["InstanceRefreshId"],
        when,
        refresh["Status"],
        refresh.get("StatusReason"),
        refresh.get("PercentageComplete"),
        refresh.get("StartTime"),
        refresh.get("EndTime"),
        json.dumps(refresh.get("Preferences"), default=str),
    )


def wait_for_settled_pool(
    autoscaling_client, asg_name: str, timeout: int = SETTLE_TIMEOUT_SECONDS
) -> None:
    """
    Wait until every instance of the ASG is in service and its warm pool is full and warmed.

    A pool that runs its own CD applies an upgrade from a settled pool. Right after the ASG
    is created its warm pool instances are still bootstrapping, and a refresh that starts then
    waits for them before it gets to the protected runners.

    :param autoscaling_client: Boto3 Auto Scaling client.
    :param asg_name: Auto Scaling group name.
    :param timeout: How long to wait, in seconds.
    :raises AssertionError: If the pool hasn't settled after ``timeout`` seconds.
    """
    deadline = time.time() + timeout
    while True:
        group = autoscaling_client.describe_auto_scaling_groups(
            AutoScalingGroupNames=[asg_name]
        )["AutoScalingGroups"][0]
        unsettled = [
            f"{instance['InstanceId']} {instance['LifecycleState']}"
            for instance in group["Instances"]
            if instance["LifecycleState"] != "InService"
        ]
        warm_pool = autoscaling_client.describe_warm_pool(AutoScalingGroupName=asg_name)
        config = warm_pool.get("WarmPoolConfiguration")
        if config:
            warmed = f"Warmed:{config['PoolState']}"
            unsettled += [
                f"{instance['InstanceId']} {instance['LifecycleState']}"
                for instance in warm_pool["Instances"]
                if instance["LifecycleState"] != warmed
            ]
            if len(warm_pool["Instances"]) < config["MinSize"]:
                unsettled.append(
                    f"warm pool has {len(warm_pool['Instances'])} of at least "
                    f"{config['MinSize']} instances"
                )
        if not unsettled:
            LOG.info("ASG %s has settled", asg_name)
            return
        assert (
            time.time() < deadline
        ), f"ASG {asg_name} hasn't settled after {timeout} seconds: {unsettled}"
        LOG.info("Waiting for ASG %s to settle: %s", asg_name, ", ".join(unsettled))
        time.sleep(POLL_SECONDS)


def wait_for_instance_refresh_end(
    autoscaling_client, asg_name: str, timeout: int = PROTECTION_HOLD_SECONDS
) -> None:
    """
    Wait until the latest instance refresh has ended, or for ``timeout`` seconds at most.

    A refresh that ignores protected instances leaves them out only while they are protected:
    if the protection is lifted before the refresh ends, the refresh replaces them after all.
    So it skips them only if it ends first, as it does at once when it has nothing else to
    replace. A refresh that waits for protected instances doesn't end while they are, and this
    returns after ``timeout`` seconds.

    :param autoscaling_client: Boto3 Auto Scaling client.
    :param asg_name: Auto Scaling group name.
    :param timeout: How long to wait at most, in seconds.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        refreshes = autoscaling_client.describe_instance_refreshes(
            AutoScalingGroupName=asg_name, MaxRecords=1
        )["InstanceRefreshes"]
        if refreshes and refreshes[0]["Status"] not in ("Pending", "InProgress"):
            LOG.info(
                "Instance refresh %s has ended: Status %s",
                refreshes[0]["InstanceRefreshId"],
                refreshes[0]["Status"],
            )
            return
        time.sleep(POLL_SECONDS)
    LOG.info(
        "The instance refresh of %s hasn't ended after %d seconds", asg_name, timeout
    )


def assert_in_service_on_asg_launch_template_version(
    autoscaling_client, asg_name: str
) -> None:
    """
    Fail unless every instance in service runs the launch template version the ASG uses.

    A refresh that skips a runner leaves it on the version from before the upgrade, while the
    ASG already points at the new one.

    :param autoscaling_client: Boto3 Auto Scaling client.
    :param asg_name: Auto Scaling group name.
    :raises AssertionError: If an instance in service is on another launch template or version.
    """
    group = autoscaling_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )["AutoScalingGroups"][0]
    assert "LaunchTemplate" in group, f"ASG {asg_name} has no launch template"
    asg_template = group["LaunchTemplate"]
    LOG.info(
        "ASG %s uses launch template %s version %s",
        asg_name,
        asg_template["LaunchTemplateId"],
        asg_template["Version"],
    )
    stale = {}
    for instance in group["Instances"]:
        if instance["LifecycleState"] != "InService":
            continue
        template = instance["LaunchTemplate"]
        LOG.info(
            "Instance %s uses launch template %s version %s",
            instance["InstanceId"],
            template["LaunchTemplateId"],
            template["Version"],
        )
        if (template["LaunchTemplateId"], template["Version"]) != (
            asg_template["LaunchTemplateId"],
            asg_template["Version"],
        ):
            stale[instance["InstanceId"]] = template["Version"]
    assert not stale, (
        f"Instances in service on a launch template version other than the ASG's "
        f"{asg_template['Version']} (instance: version): {stale}"
    )


def write_main_tf(
    terraform_module_dir: str,
    source: str,
    version: Optional[str] = None,
    root_volume_size: Optional[int] = None,
) -> None:
    """
    Generate main.tf for the test with specified source and version.

    :param terraform_module_dir: Directory where main.tf should be written.
    :param source: Terraform module source (registry or local path).
    :param version: Module version (only used with registry source).
    :param root_volume_size: The module's ``root_volume_size``. None keeps the module's default.
    """
    version_line = f'  version = "{version}"\n' if version else ""
    root_volume_line = (
        f"root_volume_size = {root_volume_size}" if root_volume_size else ""
    )

    with open(osp.join(terraform_module_dir, "main.tf"), "w") as fp:
        fp.write(dedent(f"""
                module "actions-runner" {{
                  source  = "{source}"
                  {version_line}
                  {root_volume_line}
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
    "from_version, root_volume_size",
    [
        pytest.param(
            "3.0.3",
            None,
            id="from-3.0.3",
            marks=pytest.mark.skip(
                reason="3.0.3 can't bootstrap a runner any more: its cloud-init 2.2.2 checks the InfraHouse "
                "noble apt key against a hard-coded fingerprint, and that key has been rotated. The repository "
                "isn't added, so puppet-code and infrahouse-toolkit don't install and the bootstrap hook "
                "times out."
            ),
        ),
        pytest.param("4.4.1", None, id="from-4.4.1"),
        # The upgrade from 5.0.0 changes nothing in the launch template, so on its own it starts
        # no instance refresh. A bigger root volume (the default is 30) changes the launch
        # template, as the next change that rolls the runners will, so the refresh runs with
        # this version's preferences.
        pytest.param("5.0.0", 40, id="from-5.0.0"),
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
    root_volume_size,
):
    """
    Test the upgrade from ``from_version`` to the current version.

    1. Deploy ``from_version``
    2. Verify the deregistration log group exists and write a test message to it
    3. Wait for the pool to settle, then protect every runner in service from scale-in, as
       ``gha_prerun.sh`` does for the runner that runs the CD job
    4. Upgrade to the current version, with ``root_volume_size`` if set
    5. Once the refresh has ended, or after ``PROTECTION_HOLD_SECONDS``, lift the
       protection, as ``gha_postrun.sh`` does
    6. Verify the log group has the same ARN and still holds the test message
    7. Verify the runners come back online after the instance refresh
    8. Verify no instance in service before the upgrade still is, and every instance in
       service is on the ASG's launch template version: the refresh waited for the protected
       runners and replaced them. After the upgrade from 4.4.1 an old runner would also hold
       credentials of the deleted instance role.
    9. Verify the instance and Lambda roles AWS reports carry the issue #109 prefixes
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

    # Generate main.tf for Phase 1 with registry source
    write_main_tf(
        terraform_module_dir,
        source="registry.infrahouse.com/infrahouse/actions-runner/aws",
        version=from_version,
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

    # Generate main.tf for Phase 2 with local source
    write_main_tf(
        terraform_module_dir, source="../..", root_volume_size=root_volume_size
    )
    LOG.info("Generated main.tf with local source")

    # The upgrade is applied by a CD job on one of the pool's own runners, and gha_prerun.sh
    # protects a runner from scale-in for the length of its job. Protect every runner in
    # service, so that the refresh the apply starts can replace none of them until the
    # protection is lifted.
    wait_for_settled_pool(autoscaling_client, asg_name)
    protected_instance_ids = in_service_instance_ids(autoscaling_client, asg_name)
    assert (
        protected_instance_ids
    ), "No instances in service to protect before the upgrade"
    old_instance_ids |= protected_instance_ids
    set_scale_in_protection(autoscaling_client, asg_name, protected_instance_ids, True)

    # Apply the migration
    with terraform_apply(
        terraform_module_dir,
        destroy_after=not keep_after,
        json_output=True,
    ) as migrated_output:
        LOG.info("Phase 2 output: %s", json.dumps(migrated_output, indent=4))
        log_instance_refresh(autoscaling_client, asg_name, "after the apply")

        # The apply starts the refresh and returns. The CD job goes on, can outlast the
        # refresh, and when it ends gha_postrun.sh lifts the protection. Lift it once the
        # refresh has ended, or after PROTECTION_HOLD_SECONDS if it waits for the runners.
        wait_for_instance_refresh_end(autoscaling_client, asg_name)
        log_instance_refresh(
            autoscaling_client, asg_name, "before the protection is lifted"
        )
        set_scale_in_protection(
            autoscaling_client,
            asg_name,
            protected_instance_ids
            & in_service_instance_ids(autoscaling_client, asg_name),
            False,
        )

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

            # ensure_runners waited for the instance refresh. A runner it skipped stays on
            # the old launch template version; after the upgrade from 4.4.1 it also holds
            # credentials of the instance role that was just deleted.
            log_instance_refresh(
                autoscaling_client, asg_name, "after the runners are online"
            )
            new_instance_ids = in_service_instance_ids(autoscaling_client, asg_name)
            LOG.info("Instances in service before the upgrade: %s", old_instance_ids)
            LOG.info("Instances in service after the upgrade: %s", new_instance_ids)
            assert not old_instance_ids & new_instance_ids, (
                f"Instances {sorted(old_instance_ids & new_instance_ids)} were in service "
                f"before the upgrade from {from_version}, protected from scale-in after the "
                "apply until the refresh ended (or for at most "
                f"{PROTECTION_HOLD_SECONDS} seconds), and still are: the instance refresh "
                "did not replace them."
            )
            assert_in_service_on_asg_launch_template_version(
                autoscaling_client, asg_name
            )

            # asg_name is the 4.x pool's, so this also proves the upgrade kept <pool>.
            assert_role_name_prefixes(
                boto3_session,
                aws_region,
                migrated_output["runner_role_arn"]["value"],
                asg_name,
                {
                    "reg": f"{asg_name}_registration",
                    "dereg": f"{asg_name}_deregistration",
                    "metric": f"{asg_name}_record_metric",
                },
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
