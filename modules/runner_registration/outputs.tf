output "lambda_name" {
  description = "Lambda function name that (de)registers runners"
  value       = aws_lambda_function.main.function_name
}
