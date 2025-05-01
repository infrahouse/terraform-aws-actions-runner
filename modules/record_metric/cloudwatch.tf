resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.lambda.function_name}"
  retention_in_days = var.cloudwatch_log_group_retention
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}
