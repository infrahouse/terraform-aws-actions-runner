locals {
  environment = "development"
}

module "actions-runner" {
  source                    = "../.."
  asg_min_size              = 1
  asg_max_size              = 1
  subnet_ids                = var.subnet_private_ids
  environment               = local.environment
  github_org_name           = var.github_org_name
  github_app_pem_secret_arn = var.github_app_pem_secret_arn
  github_token_secret_arn   = var.github_token != null ? aws_secretsmanager_secret.github_token.arn : null
  puppet_hiera_config_path  = "/opt/infrahouse-puppet-data/environments/${local.environment}/hiera.yaml"
  packages = [
    "infrahouse-puppet-data"
  ]
  extra_labels  = ["awesome"]
  github_app_id = var.github_app_id
}
