locals {
  module_version = "6.1.0"

  lts_codenames = ["noble"]

  module_marker = "infrahouse/actions-runner/aws"
  default_module_tags = merge(
    {
      environment : var.environment
      service : "actions-runner"
      account : data.aws_caller_identity.current.account_id
      created_by_module : local.module_marker
    },
    var.tags
  )
  ami_name_pattern = contains(local.lts_codenames, var.ubuntu_codename) ? (
    "ubuntu-pro-server/images/hvm-ssd-gp3/ubuntu-${var.ubuntu_codename}-*"
    ) : (
    "ubuntu/images/hvm-ssd-gp3/ubuntu-${var.ubuntu_codename}-*"
  )

  ami_id             = var.ami_id == null ? data.aws_ami.ubuntu.id : var.ami_id
  instance_memory_gb = ceil(data.aws_ec2_instance_type.this.memory_size / 1024.0)
  # Extra space on root volume for OS, packages, and swap beyond what hibernation needs for RAM
  hibernation_volume_overhead_gb = 10
  asg_min                        = var.asg_min_size != null ? var.asg_min_size : length(var.subnet_ids)
  asg_max                        = var.asg_max_size != null ? var.asg_max_size : length(var.subnet_ids) + 1
  warm_pool_max                  = var.warm_pool_max_size != null ? var.warm_pool_max_size : local.asg_max
  # +1 ensures at least one pre-warmed instance is always available during scale-out
  warm_pool_min = min(
    var.warm_pool_min_size != null ? var.warm_pool_min_size : var.idle_runners_target_count + 1,
    local.warm_pool_max
  )

  # Issue #109: every Lambda execution role is named actions-runner-lambda-<pool>-<function>-<suffix>,
  # so a policy can select this pool's Lambdas, or every pool's, by name pattern without also
  # selecting an instance role (those start with actions-runner-instance-).
  # <pool> is random_string.asg_name, the suffix of the ASG name. Unlike the ASG name, which embeds
  # the launch template's timestamped name, it doesn't change when the launch template is replaced.
  lambda_role_name_prefix = "actions-runner-lambda-${random_string.asg_name.result}-"
  # lambda-monitored accepts at most 38 characters, which AWS's 64-character role name limit leaves
  # after Terraform's 26-character suffix. The longest is 22 + 6 (pool) + 1 + 6 ("metric") + 1 = 36.
  registration_role_name_prefix   = "${local.lambda_role_name_prefix}reg-"
  deregistration_role_name_prefix = "${local.lambda_role_name_prefix}dereg-"
  record_metric_role_name_prefix  = "${local.lambda_role_name_prefix}metric-"

  # Issue #111: the instance role is named from the same <pool>, so a pool's roles read as a set and the
  # name is unique per pool without any input from the caller. The fixed prefix keeps it out of the
  # actions-runner-lambda-* namespace above. instance-profile uses it verbatim as the role name.
  instance_role_name = "actions-runner-instance-${random_string.asg_name.result}"

  registration_token_secret_prefix = "GH-reg-token-${random_string.reg_token_suffix.result}"
  registration_hookname            = "registration"
  deregistration_hookname          = "deregistration"
  bootstrap_hookname               = "bootstrap"

  # Inspector scans a new instance roughly 70-80 seconds into boot, minutes
  # before profile::github_runner finishes applying security upgrades. Without
  # this tag every launch reports vulnerabilities that are already fixed by the
  # time anyone reads the finding.
  inspector_exclusion_tag = "InspectorEc2Exclusion"

  # Runs from post_runcmd, which the cloud-init bootstrap script executes before
  # it signals the bootstrap lifecycle hook. A failure here trips that script's
  # ERR trap and the instance is ABANDONed, so an instance cannot reach service
  # while still excluded from scanning.
  inspector_unexclude_cmd = join(" ", [
    "aws ec2 delete-tags",
    "--region ${data.aws_region.current.name}",
    "--resources $(ec2metadata --instance-id)",
    "--tags Key=${local.inspector_exclusion_tag}",
  ])

  all_alarm_topic_arns = concat(
    [aws_sns_topic.alarms.arn],
    var.alarm_topic_arns,
  )
}
