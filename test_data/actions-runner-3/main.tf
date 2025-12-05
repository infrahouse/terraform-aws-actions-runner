
module "actions-runner" {
  source = "../.."

  instance_type             = "t3a.small"
  asg_min_size              = 1
  asg_max_size              = var.asg_max_size
  subnet_ids                = var.subnet_ids
  lambda_subnet_ids         = var.lambda_subnet_ids
  environment               = local.environment
  github_org_name           = var.github_org_name
  github_app_pem_secret_arn = var.github_app_pem_secret_arn
  github_token_secret_arn   = var.github_token != null ? aws_secretsmanager_secret.github_token.arn : null
  puppet_hiera_config_path  = "/opt/infrahouse-puppet-data/environments/${local.environment}/hiera.yaml"
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
}
