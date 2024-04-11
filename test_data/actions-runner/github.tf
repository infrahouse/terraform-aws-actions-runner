resource "aws_secretsmanager_secret" "github_token" {
  description             = "GitHub token"
  name                    = "GITHUB_TOKEN"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "github_token" {
  secret_id     = aws_secretsmanager_secret.github_token.id
  secret_string = data.external.env.result["GITHUB_TOKEN"]
}
