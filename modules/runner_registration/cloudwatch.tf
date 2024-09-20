resource "aws_cloudwatch_event_rule" "scale" {
  name_prefix = "asg-scale"
  description = "ASG lifecycle hook"
  event_pattern = jsonencode(
    {
      "source" : ["aws.autoscaling"],
      "detail-type" : [
        "EC2 Instance-terminate Lifecycle Action",
        "EC2 Instance-launch Lifecycle Action",
      ],
      "detail" : {
        "AutoScalingGroupName" : [
          var.asg_name
        ]
      }
    }
  )
  tags = var.tags
}

resource "aws_cloudwatch_event_target" "scale-in-out" {
  arn  = aws_lambda_function.main.arn
  rule = aws_cloudwatch_event_rule.scale.name
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.main.function_name}"
  retention_in_days = 14
  tags              = var.tags
}
