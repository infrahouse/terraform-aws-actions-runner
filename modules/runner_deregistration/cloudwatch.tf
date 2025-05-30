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

resource "aws_cloudwatch_event_target" "scale-in-out" {
  arn  = aws_lambda_function.lambda.arn
  rule = aws_cloudwatch_event_rule.scale.name
}

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
