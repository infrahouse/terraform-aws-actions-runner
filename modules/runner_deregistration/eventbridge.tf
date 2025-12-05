# Scheduled EventBridge Rule (every 30 minutes)

locals {
  factor = 30
  period = local.factor == 1 ? "minute" : "minutes"
}

resource "aws_cloudwatch_event_rule" "run_every" {
  name_prefix         = substr("${var.asg_name}-${local.factor}-${local.period}", 0, 38)
  description         = "Trigger Lambda ${module.lambda_monitored.lambda_function_name} every ${local.factor} ${local.period}"
  schedule_expression = "rate(${local.factor} ${local.period})"
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}

# Attach Lambda as a target of the scheduled rule
resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.run_every.name
  target_id = "send-to-lambda"
  arn       = module.lambda_monitored.lambda_function_arn
}

# Grant EventBridge permission to invoke the Lambda (scheduled)
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = module.lambda_monitored.lambda_function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.run_every.arn
}

# Lifecycle Hook EventBridge Rule (ASG instance termination)

resource "aws_cloudwatch_event_rule" "scale" {
  name_prefix = substr("${var.asg_name}-", 0, 38)
  description = "ASG lifecycle hook"
  event_pattern = jsonencode(
    {
      "source" : ["aws.autoscaling"],
      "detail-type" : [
        "EC2 Instance-terminate Lifecycle Action",
        # "EC2 Instance-launch Lifecycle Action",
      ],
      "detail" : {
        "AutoScalingGroupName" : [
          var.asg_name
        ]
      }
    }
  )
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}

# Attach Lambda as a target of the lifecycle hook rule
resource "aws_cloudwatch_event_target" "scale-in-out" {
  arn  = module.lambda_monitored.lambda_function_arn
  rule = aws_cloudwatch_event_rule.scale.name
}

# Grant EventBridge permission to invoke the Lambda (lifecycle hook)
resource "aws_lambda_permission" "allow_cloudwatch_asg_lifecycle_hook" {
  action        = "lambda:InvokeFunction"
  function_name = module.lambda_monitored.lambda_function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.scale.arn
}
