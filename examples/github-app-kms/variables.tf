variable "region" {
  description = "AWS region to create the runners in."
  type        = string
}

variable "environment" {
  description = "Environment name, e.g. production. The module passes it on to Puppet."
  type        = string
}

variable "github_org_name" {
  description = "GitHub organization the runners register with."
  type        = string
}

variable "subnet_ids" {
  description = <<-EOT
    Private subnets for the runners and the module's Lambda functions. They need a NAT gateway
    (or VPC endpoints) to reach GitHub and the AWS APIs.
  EOT
  type        = list(string)
}

variable "alarm_emails" {
  description = "Email addresses that receive the pool's alarms."
  type        = list(string)
}

variable "github_app_kms_key_arn" {
  description = <<-EOT
    ARN of the KMS key that holds the GitHub App's private key: an RSA_2048 SIGN_VERIFY key in the region of the
    runners, with the App's key imported as the module's README describes. A key ARN, not an alias.
  EOT
  type        = string
}

variable "github_app_id" {
  description = "ID of the GitHub App, from its settings page."
  type        = number
}

variable "puppet_hiera_config_path" {
  description = <<-EOT
    Path, on the runner, to the hiera.yaml of your Puppet data. For the gha_runner role, the data must set
    profile::github_runner::url and profile::github_runner::org to your organization.
  EOT
  type        = string
}

variable "packages" {
  description = "APT packages to install before Puppet runs, e.g. the package that ships your Puppet data."
  type        = list(string)
  default     = []
}
