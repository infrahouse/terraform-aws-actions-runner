locals {
  module_version = "3.0.3"

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

  ami_id                           = var.ami_id == null ? data.aws_ami.ubuntu.id : var.ami_id
  registration_token_secret_prefix = "GH-reg-token-${random_string.reg_token_suffix.result}"
  registration_hookname            = "registration"
  deregistration_hookname          = "deregistration"
  bootstrap_hookname               = "bootstrap"
}
