resource "aws_cloudwatch_metric_alarm" "cpu_utilization_alarm" {
  count               = var.sns_topic_alarm_arn != null ? 1 : 0
  alarm_name          = format("CPU Alarm on ASG %s", aws_autoscaling_group.actions-runner.name)
  comparison_operator = "GreaterThanThreshold"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  evaluation_periods  = 1
  period              = 60
  threshold           = 90
  namespace           = "AWS/EC2"
  alarm_actions       = [var.sns_topic_alarm_arn]
  alarm_description   = format("%s alarm - CPU exceeds 90 percent", aws_autoscaling_group.actions-runner.name)
  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
  }
}

module "record_metric" {
  source                         = "./modules/record_metric"
  asg_name                       = aws_autoscaling_group.actions-runner.name
  cloudwatch_log_group_retention = var.cloudwatch_log_group_retention
  architecture                   = var.architecture
  python_version                 = var.python_version

  github_org_name = var.github_org_name
  github_credentials = {
    type : var.github_token_secret_arn != null ? "token" : "pem"
    secret : var.github_token_secret_arn != null ? var.github_token_secret_arn : var.github_app_pem_secret_arn
  }
  github_app_id = var.github_app_id

  alarm_emails         = var.alarm_emails
  error_rate_threshold = var.error_rate_threshold

  tags            = local.default_module_tags
  installation_id = random_uuid.installation-id.result
}
