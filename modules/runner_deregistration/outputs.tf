output "lambda_name" {
  value = module.lambda_monitored.lambda_function_name
}

output "log_group_name" {
  description = "CloudWatch Log Group name for the deregistration lambda"
  value       = module.lambda_monitored.cloudwatch_log_group_name
}
