module "registration" {
  source          = "./modules/runner_registration"
  asg_name        = local.asg_name
  github_org_name = var.github_org_name
  github_credentials = {
    type : var.github_token_secret_arn != null ? "token" : "pem"
    secret : var.github_token_secret_arn != null ? var.github_token_secret_arn : var.github_app_pem_secret_arn
  }
  registration_token_secret_prefix = local.registration_token_secret_prefix
  lambda_timeout                   = var.allowed_drain_time
  tags                             = local.default_module_tags
  github_app_id                    = var.github_app_id
  python_version                   = var.python_version
}
