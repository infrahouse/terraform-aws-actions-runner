resource "aws_cloudwatch_event_rule" "scale" {
  name_prefix = substr("${var.asg_name}-", 0, 38)
  description = "ASG lifecycle hook"
  event_pattern = jsonencode(
    {
      "source" : ["aws.autoscaling"],
      "detail-type" : [
        # "EC2 Instance-terminate Lifecycle Action",
        "EC2 Instance-launch Lifecycle Action",
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
  arn  = module.lambda_monitored.lambda_function_arn
  rule = aws_cloudwatch_event_rule.scale.name
}

resource "aws_lambda_permission" "allow_cloudwatch_asg_lifecycle_hook" {
  action        = "lambda:InvokeFunction"
  function_name = module.lambda_monitored.lambda_function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.scale.arn
}
