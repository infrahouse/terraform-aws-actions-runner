locals {
  environment = "development"
}

module "actions-runner" {
  source                   = "../.."
  asg_min_size             = 1
  asg_max_size             = 1
  subnet_ids               = var.subnet_private_ids
  environment              = local.environment
  github_org_name          = "infrahouse"
  github_token_secret_arn  = aws_secretsmanager_secret.github_token.arn
  keypair_name             = aws_key_pair.jumphost.key_name
  puppet_hiera_config_path = "/opt/infrahouse-puppet-data/environments/${local.environment}/hiera.yaml"
  packages = [
    "infrahouse-puppet-data"
  ]
  extra_labels = ["awesome"]
}
