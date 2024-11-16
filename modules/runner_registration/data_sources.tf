data "aws_secretsmanager_secret" "github" {
  arn = var.github_credentials.secret
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
