output "registration_token_secret_prefix" {
  description = "The prefix used for storing GitHub Actions runner registration token secrets in AWS Secrets Manager"
  value       = module.actions-runner.registration_token_secret_prefix
}

output "registration_lambda_name" {
  description = "Name of the runner_registration lambda function."
  value       = module.actions-runner.registration_lambda_name
}

output "deregistration_lambda_name" {
  description = "Name of the runner_deregistration lambda function."
  value       = module.actions-runner.deregistration_lambda_name
}

output "record_metric_lambda_name" {
  description = "Name of the record_metric lambda function."
  value       = module.actions-runner.record_metric_lambda_name
}
