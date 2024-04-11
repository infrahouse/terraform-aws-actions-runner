module "deregister" {
  source              = "./modules/deregister_runner"
  asg_name            = aws_autoscaling_group.actions-runner.name
  github_org_name     = var.github_org_name
  github_token_secret = data.aws_secretsmanager_secret.github_token.name
}
