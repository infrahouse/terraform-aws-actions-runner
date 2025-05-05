resource "aws_cloudwatch_metric_alarm" "idle_runners_low" {
  alarm_name          = "IdleRunnersTooLow-${aws_autoscaling_group.actions-runner.name}"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "IdleRunners"
  namespace           = "GitHubRunners"
  period              = 60
  statistic           = "Average"
  threshold           = var.idle_runners_target_count
  alarm_description   = "Idle runners below safe threshold"
  dimensions = {
    asg_name = aws_autoscaling_group.actions-runner.name
  }

  alarm_actions = [aws_autoscaling_policy.scale_out.arn]
}

resource "aws_cloudwatch_metric_alarm" "idle_runners_high" {
  alarm_name          = "IdleRunnersTooHigh-${aws_autoscaling_group.actions-runner.name}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "IdleRunners"
  namespace           = "GitHubRunners"
  period              = 60
  statistic           = "Average"
  threshold           = var.idle_runners_target_count
  alarm_description   = "Too many idle runners"
  dimensions = {
    asg_name = aws_autoscaling_group.actions-runner.name
  }

  alarm_actions = [aws_autoscaling_policy.scale_in.arn]
}

resource "aws_autoscaling_policy" "scale_out" {
  name                   = "scale-out-idle-runners"
  scaling_adjustment     = 1
  adjustment_type        = "ChangeInCapacity"
  cooldown               = 180
  autoscaling_group_name = aws_autoscaling_group.actions-runner.name
  policy_type            = "SimpleScaling"
}

resource "aws_autoscaling_policy" "scale_in" {
  name                   = "scale-in-idle-runners"
  scaling_adjustment     = -1
  adjustment_type        = "ChangeInCapacity"
  cooldown               = 180
  autoscaling_group_name = aws_autoscaling_group.actions-runner.name
  policy_type            = "SimpleScaling"
}
