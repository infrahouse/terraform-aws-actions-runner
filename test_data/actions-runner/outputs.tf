output "registration_token_secret_prefix" {
  description = "The prefix used for storing GitHub Actions runner registration token secrets in AWS Secrets Manager"
  value       = module.actions-runner.registration_token_secret_prefix
}
