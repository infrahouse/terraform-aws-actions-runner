module "registration" {
  source                           = "./modules/runner_registration"
  asg_name                         = local.asg_name
  github_org_name                  = var.github_org_name
  github_token_secret              = data.aws_secretsmanager_secret.github_token.name
  registration_token_secret_prefix = local.registration_token_secret_prefix
  tags                             = local.default_module_tags
}
