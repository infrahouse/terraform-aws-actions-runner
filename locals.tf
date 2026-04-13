locals {
  module_version = "3.4.3"

  lts_codenames = ["noble"]

  default_module_tags = merge(
    {
      environment : var.environment
      service : "actions-runner"
      account : data.aws_caller_identity.current.account_id
      created_by_module : "infrahouse/actions-runner/aws"
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
  asg_max                        = var.asg_max_size != null ? var.asg_max_size : length(var.subnet_ids) + 1
  warm_pool_max                  = var.warm_pool_max_size != null ? var.warm_pool_max_size : local.asg_max
  # +1 ensures at least one pre-warmed instance is always available during scale-out
  warm_pool_min = min(
    var.warm_pool_min_size != null ? var.warm_pool_min_size : var.idle_runners_target_count + 1,
    local.warm_pool_max
  )

  registration_token_secret_prefix = "GH-reg-token-${random_string.reg_token_suffix.result}"
  registration_hookname            = "registration"
  deregistration_hookname          = "deregistration"
  bootstrap_hookname               = "bootstrap"
}
