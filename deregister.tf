module "registration" {
  source                         = "./modules/runner_registration"
  asg_name                       = local.asg_name
  cloudwatch_log_group_retention = var.cloudwatch_log_group_retention
  github_org_name                = var.github_org_name
  github_credentials = {
    type : var.github_token_secret_arn != null ? "token" : "pem"
    secret : var.github_token_secret_arn != null ? var.github_token_secret_arn : var.github_app_pem_secret_arn
  }
  github_app_id                    = var.github_app_id
  registration_token_secret_prefix = local.registration_token_secret_prefix
  lambda_bucket_name               = aws_s3_bucket.lambda_tmp.bucket
  lambda_timeout                   = var.allowed_drain_time
  tags                             = local.default_module_tags
  python_version                   = var.python_version
}
