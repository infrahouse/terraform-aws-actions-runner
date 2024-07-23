data "aws_secretsmanager_secret" "github_token" {
  name = var.github_token_secret
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
