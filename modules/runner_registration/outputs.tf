output "lambda_name" {
  description = "Lambda function name that (de)registers runners"
  value       = module.lambda_monitored.lambda_function_name
}
