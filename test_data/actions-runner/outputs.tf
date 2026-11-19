output "runner_role_arn" {
  description = "ARN of the runner instance role."
  value       = module.actions-runner.runner_role_arn
}

output "runner_role_name" {
  description = "Name of the runner instance role."
  value       = module.actions-runner.runner_role_name
}

output "lambda_role_name_prefix" {
  description = "Name prefix of the pool's Lambda execution roles."
  value       = module.actions-runner.lambda_role_name_prefix
}

output "lambda_role_arn_pattern" {
  description = "ARN pattern that matches the pool's Lambda execution roles."
  value       = module.actions-runner.lambda_role_arn_pattern
}

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
