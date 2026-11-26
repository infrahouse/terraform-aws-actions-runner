# Fails the plan early if the credential secret doesn't exist. A KMS key isn't a secret.
data "aws_secretsmanager_secret" "github" {
  count = var.github_credentials.type == "kms" ? 0 : 1
  arn   = var.github_credentials.arn
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
