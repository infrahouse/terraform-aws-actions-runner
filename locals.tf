locals {
  default_module_tags = {
    environment : var.environment
    service : var.service_name
    account : data.aws_caller_identity.current.account_id
    created_by_module : "infrahouse/actions-runner/aws"
  }

  ami_id                           = var.ami_id == null ? data.aws_ami.ubuntu.id : var.ami_id
  registration_token_secret_prefix = "GH-reg-token-${random_string.reg_token_suffix.result}"
}
