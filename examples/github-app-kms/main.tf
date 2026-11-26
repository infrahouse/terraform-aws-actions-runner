terraform {
  required_version = "~> 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

module "actions_runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "6.0.0"

  environment     = var.environment
  github_org_name = var.github_org_name
  subnet_ids      = var.subnet_ids
  alarm_emails    = var.alarm_emails

  # The private key of a GitHub App installed in the organization, imported into a KMS key. The Lambdas sign with
  # kms:Sign and never hold the key. See "GitHub App key in KMS" in the module's README.
  github_app_kms_key_arn = var.github_app_kms_key_arn
  github_app_id          = var.github_app_id

  # Puppet configures the runners. Its Hiera data must set profile::github_runner::url and
  # profile::github_runner::org for the gha_runner role; see README.md.
  puppet_hiera_config_path = var.puppet_hiera_config_path
  packages                 = var.packages

  instance_type = "t3a.small"
  asg_min_size  = 1
  asg_max_size  = 2
}
