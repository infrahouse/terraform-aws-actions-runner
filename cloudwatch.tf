resource "aws_cloudwatch_metric_alarm" "cpu_utilization_alarm" {
  alarm_name          = format("CPU Alarm on ASG %s", aws_autoscaling_group.actions-runner.name)
  comparison_operator = "GreaterThanThreshold"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  evaluation_periods  = 5
  period              = 60
  threshold           = 90
  namespace           = "AWS/EC2"
  alarm_actions       = local.all_alarm_topic_arns
  alarm_description   = format("%s alarm - CPU exceeds 90 percent", aws_autoscaling_group.actions-runner.name)
  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
  }
}

# Skipped on fixed-size pools (asg_min == asg_max) where sitting at max is
# the intended steady state and would alarm continuously.
resource "aws_cloudwatch_metric_alarm" "asg_at_max" {
  count = local.asg_min < local.asg_max ? 1 : 0

  alarm_name          = "ASGAtMax-${aws_autoscaling_group.actions-runner.name}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  metric_name         = "GroupInServiceInstances"
  namespace           = "AWS/AutoScaling"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 5
  threshold           = local.asg_max
  alarm_description   = "ASG ${aws_autoscaling_group.actions-runner.name} pinned at max size for 5 minutes — raise ceiling or investigate stuck jobs"
  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
  }
  alarm_actions      = local.all_alarm_topic_arns
  treat_missing_data = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "asg_zero_in_service" {
  alarm_name          = "ASGZeroInService-${aws_autoscaling_group.actions-runner.name}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 10
  alarm_description   = "ASG ${aws_autoscaling_group.actions-runner.name} has 0 instances in service while desired capacity > 0 for 10 minutes"
  alarm_actions       = local.all_alarm_topic_arns
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "e1"
    expression  = "IF(m_is < 1 AND m_dc > 0, 1, 0)"
    label       = "In-service dropped to zero with desired > 0"
    return_data = true
  }

  metric_query {
    id = "m_is"
    metric {
      metric_name = "GroupInServiceInstances"
      namespace   = "AWS/AutoScaling"
      period      = 60
      stat        = "Average"
      dimensions = {
        AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
      }
    }
  }

  metric_query {
    id = "m_dc"
    metric {
      metric_name = "GroupDesiredCapacity"
      namespace   = "AWS/AutoScaling"
      period      = 60
      stat        = "Average"
      dimensions = {
        AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
      }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "warm_pool_empty" {
  count = var.on_demand_base_capacity == null ? 1 : 0

  alarm_name          = "WarmPoolEmpty-${aws_autoscaling_group.actions-runner.name}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 10
  alarm_description   = "Warm pool for ${aws_autoscaling_group.actions-runner.name} is empty (warmed = 0 while desired > 0) — the latency-hiding optimization is off"
  alarm_actions       = local.all_alarm_topic_arns
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "e1"
    expression  = "IF(m_warmed < 1 AND m_desired > 0, 1, 0)"
    label       = "Warm pool drained"
    return_data = true
  }

  metric_query {
    id = "m_warmed"
    metric {
      metric_name = "WarmPoolWarmedCapacity"
      namespace   = "AWS/AutoScaling"
      period      = 60
      stat        = "Average"
      dimensions = {
        AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
      }
    }
  }

  metric_query {
    id = "m_desired"
    metric {
      metric_name = "WarmPoolDesiredCapacity"
      namespace   = "AWS/AutoScaling"
      period      = 60
      stat        = "Average"
      dimensions = {
        AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
      }
    }
  }
}

# Puppet provisioning can legitimately run up to ~15 min on cold boot; the
# 20-minute sustained threshold sits above that ceiling so normal scale-out
# doesn't trip it.
resource "aws_cloudwatch_metric_alarm" "asg_launch_stuck" {
  alarm_name          = "ASGLaunchStuck-${aws_autoscaling_group.actions-runner.name}"
  comparison_operator = "GreaterThanThreshold"
  metric_name         = "GroupPendingInstances"
  namespace           = "AWS/AutoScaling"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 20
  threshold           = 0
  alarm_description   = "ASG ${aws_autoscaling_group.actions-runner.name} has pending instances stuck for >20 minutes — likely launch failure (bad launch template, capacity, IAM)"
  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
  }
  alarm_actions      = local.all_alarm_topic_arns
  treat_missing_data = "notBreaching"
}

# Instances only reach InService after the Launch lifecycle hook completes
# (runner_registration Lambda), so a registered runner should exist by then.
# A sustained gap catches the "EC2 up but never registered" class.
#
# Threshold > 1 (not > 0) absorbs brief single-instance transients — most
# commonly an instance waking from warm-pool hibernation: for ~30–60s the
# instance is InService but the runner agent hasn't re-announced online to
# GitHub yet, so m_is=1 and m_busy+m_idle=0. The 5-period sustained window
# already covers this, but > 1 adds a belt to the suspenders. The real
# #93 failure mode is a permanent gap, which trips any positive threshold.
resource "aws_cloudwatch_metric_alarm" "runner_registration_gap" {
  alarm_name          = "RunnerRegistrationGap-${aws_autoscaling_group.actions-runner.name}"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 1
  evaluation_periods  = 5
  alarm_description   = "Registered runners more than 1 fewer than InService EC2 instances for >5 minutes on ASG ${aws_autoscaling_group.actions-runner.name} — instances launched but never registered with GitHub"
  alarm_actions       = local.all_alarm_topic_arns
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "e1"
    expression  = "m_is - (m_busy + m_idle)"
    label       = "InService - (Busy + Idle)"
    return_data = true
  }

  metric_query {
    id = "m_is"
    metric {
      metric_name = "GroupInServiceInstances"
      namespace   = "AWS/AutoScaling"
      period      = 60
      stat        = "Average"
      dimensions = {
        AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
      }
    }
  }

  metric_query {
    id = "m_busy"
    metric {
      metric_name = "BusyRunners"
      namespace   = "GitHubRunners"
      period      = 60
      stat        = "Average"
      dimensions = {
        asg_name = aws_autoscaling_group.actions-runner.name
      }
    }
  }

  metric_query {
    id = "m_idle"
    metric {
      metric_name = "IdleRunners"
      namespace   = "GitHubRunners"
      period      = 60
      stat        = "Average"
      dimensions = {
        asg_name = aws_autoscaling_group.actions-runner.name
      }
    }
  }
}

# Distinct from IdleRunnersTooLow (which triggers scale-out): here we're
# already at max size with zero idle runners, so scale-out can't help and
# jobs are queueing. Skipped on fixed-size pools where scaling isn't
# available — saturation is the intended steady state.
resource "aws_cloudwatch_metric_alarm" "asg_saturated_at_max" {
  count = local.asg_min < local.asg_max ? 1 : 0

  alarm_name          = "ASGSaturatedAtMax-${aws_autoscaling_group.actions-runner.name}"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 10
  alarm_description   = "ASG ${aws_autoscaling_group.actions-runner.name} at max size with all runners busy for 10 minutes — scale-out cannot help; jobs queueing"
  alarm_actions       = local.all_alarm_topic_arns
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "e1"
    expression  = "IF(m_is >= ${local.asg_max} AND m_busy >= m_is, 1, 0)"
    label       = "At max size and fully utilized"
    return_data = true
  }

  metric_query {
    id = "m_is"
    metric {
      metric_name = "GroupInServiceInstances"
      namespace   = "AWS/AutoScaling"
      period      = 60
      stat        = "Average"
      dimensions = {
        AutoScalingGroupName = aws_autoscaling_group.actions-runner.name
      }
    }
  }

  metric_query {
    id = "m_busy"
    metric {
      metric_name = "BusyRunners"
      namespace   = "GitHubRunners"
      period      = 60
      stat        = "Average"
      dimensions = {
        asg_name = aws_autoscaling_group.actions-runner.name
      }
    }
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
