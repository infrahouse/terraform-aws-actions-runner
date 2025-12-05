output "registration_token_secret_prefix" {
  description = "The prefix used for storing GitHub Actions runner registration token secrets in AWS Secrets Manager"
  value       = module.actions-runner.registration_token_secret_prefix
}

output "autoscaling_group_name" {
  description = "Autoscaling group name"
  value       = module.actions-runner.autoscaling_group_name
}