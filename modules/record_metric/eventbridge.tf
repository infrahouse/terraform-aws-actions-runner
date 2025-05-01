# CloudWatch EventBridge Rule (every minute)

locals {
  period = "minute"
}

resource "aws_cloudwatch_event_rule" "run_every" {
  name                = "run-every-${local.period}"
  description         = "Trigger Lambda ${aws_lambda_function.lambda.function_name} every ${local.period}"
  schedule_expression = "rate(1 ${local.period})"
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}

# Attach Lambda as a target of the rule
resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.run_every.name
  target_id = "send-to-lambda"
  arn       = aws_lambda_function.lambda.arn
}

# Grant EventBridge permission to invoke the Lambda
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.run_every.arn
}
