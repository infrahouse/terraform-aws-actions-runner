locals {
  dashboard_region  = data.aws_region.current.name
  warm_pool_enabled = var.on_demand_base_capacity == null

  dashboard_alarm_arns = concat(
    [
      aws_cloudwatch_metric_alarm.cpu_utilization_alarm.arn,
      aws_cloudwatch_metric_alarm.asg_at_max.arn,
      aws_cloudwatch_metric_alarm.asg_zero_in_service.arn,
      aws_cloudwatch_metric_alarm.asg_launch_stuck.arn,
      aws_cloudwatch_metric_alarm.runner_registration_gap.arn,
      aws_cloudwatch_metric_alarm.asg_saturated_at_max.arn,
      aws_cloudwatch_metric_alarm.idle_runners_low.arn,
      aws_cloudwatch_metric_alarm.idle_runners_high.arn,
    ],
    aws_cloudwatch_metric_alarm.warm_pool_empty[*].arn,
  )

  dashboard_identity_line = join(" · ", compact([
    "**env:** ${var.environment}",
    length(var.extra_labels) > 0 ? "**labels:** ${join(", ", var.extra_labels)}" : "",
    "**region:** ${local.dashboard_region}",
  ]))

  dashboard_widgets = concat(
    # -----------------------------------------------------------------------
    # Identity + Alarms
    # -----------------------------------------------------------------------
    [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2
        properties = {
          markdown = "# ${local.asg_name}\n${local.dashboard_identity_line}"
        }
      },
      {
        type   = "text"
        x      = 0
        y      = 2
        width  = 24
        height = 1
        properties = {
          markdown = "## Alarms"
        }
      },
      {
        type   = "alarm"
        x      = 0
        y      = 3
        width  = 24
        height = 4
        properties = {
          title  = "Alarm state"
          alarms = local.dashboard_alarm_arns
        }
      },

      # -----------------------------------------------------------------------
      # GitHub
      # -----------------------------------------------------------------------
      {
        type   = "text"
        x      = 0
        y      = 7
        width  = 24
        height = 1
        properties = {
          markdown = "## GitHub"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 12
        height = 6
        properties = {
          title  = "Runners (Idle / Busy) with scale thresholds"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["GitHubRunners", "IdleRunners", "asg_name", local.asg_name, { label = "Idle", stat = "Average" }],
            [".", "BusyRunners", ".", ".", { label = "Busy", stat = "Average" }],
          ]
          annotations = {
            horizontal = [
              { value = var.idle_runners_target_count, label = "Idle scale target (low / high)", color = "#d62728" },
            ]
          }
          yAxis = {
            left = { min = 0 }
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 8
        width  = 12
        height = 6
        properties = {
          title  = "Utilization %"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            [
              {
                expression = "IF((busy + idle) > 0, 100 * busy / (busy + idle), 0)"
                label      = "Utilization %"
                id         = "util"
              }
            ],
            ["GitHubRunners", "BusyRunners", "asg_name", local.asg_name, { id = "busy", visible = false, stat = "Average" }],
            [".", "IdleRunners", ".", ".", { id = "idle", visible = false, stat = "Average" }],
          ]
          yAxis = {
            left = { min = 0, max = 100 }
          }
        }
      },

      # -----------------------------------------------------------------------
      # ASG
      # -----------------------------------------------------------------------
      {
        type   = "text"
        x      = 0
        y      = 14
        width  = 24
        height = 1
        properties = {
          markdown = "## ASG"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 15
        width  = 24
        height = 6
        properties = {
          title  = "Fleet size (capacity, left) and transients (count, right)"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["AWS/AutoScaling", "GroupDesiredCapacity", "AutoScalingGroupName", local.asg_name, { label = "Desired", stat = "Average" }],
            [".", "GroupInServiceInstances", ".", ".", { label = "In service", stat = "Average" }],
            [".", "GroupMinSize", ".", ".", { label = "Min", stat = "Average" }],
            [".", "GroupMaxSize", ".", ".", { label = "Max", stat = "Average" }],
            [".", "GroupPendingInstances", ".", ".", { label = "Pending (r)", stat = "Average", yAxis = "right" }],
            [".", "GroupTerminatingInstances", ".", ".", { label = "Terminating (r)", stat = "Average", yAxis = "right" }],
            [".", "GroupStandbyInstances", ".", ".", { label = "Standby (r)", stat = "Average", yAxis = "right" }],
          ]
          yAxis = {
            left  = { min = 0, label = "Capacity" }
            right = { min = 0, label = "Transient count" }
          }
        }
      },
    ],
    local.warm_pool_enabled ? [
      {
        type   = "metric"
        x      = 0
        y      = 21
        width  = 12
        height = 6
        properties = {
          title  = "Warm pool"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["AWS/AutoScaling", "WarmPoolDesiredCapacity", "AutoScalingGroupName", local.asg_name, { label = "Desired", stat = "Average" }],
            [".", "WarmPoolWarmedCapacity", ".", ".", { label = "Warmed", stat = "Average" }],
            [".", "WarmPoolPendingCapacity", ".", ".", { label = "Pending", stat = "Average" }],
            [".", "WarmPoolTerminatingCapacity", ".", ".", { label = "Terminating", stat = "Average" }],
          ]
          yAxis = {
            left = { min = 0 }
          }
        }
      },
    ] : [],
    [
      {
        type   = "metric"
        x      = local.warm_pool_enabled ? 12 : 0
        y      = 21
        width  = local.warm_pool_enabled ? 12 : 24
        height = 6
        properties = {
          title  = "EC2 status check failures"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["AWS/EC2", "StatusCheckFailed", "AutoScalingGroupName", local.asg_name, { label = "StatusCheckFailed (sum)", stat = "Sum" }],
          ]
          yAxis = {
            left = { min = 0 }
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 27
        width  = 24
        height = 6
        properties = {
          title  = "EC2 CPU (fleet)"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", local.asg_name, { label = "Average", stat = "Average" }],
            ["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", local.asg_name, { label = "p95", stat = "p95" }],
          ]
          yAxis = {
            left = { min = 0, max = 100, label = "CPU %" }
          }
        }
      },

      # -----------------------------------------------------------------------
      # Lambda
      # -----------------------------------------------------------------------
      {
        type   = "text"
        x      = 0
        y      = 33
        width  = 24
        height = 1
        properties = {
          markdown = "## Lambda"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 34
        width  = 8
        height = 6
        properties = {
          title  = "Invocations"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", module.registration.lambda_name, { label = "registration", stat = "Sum" }],
            [".", ".", ".", module.deregistration.lambda_name, { label = "deregistration", stat = "Sum" }],
            [".", ".", ".", module.record_metric.lambda_name, { label = "record_metric", stat = "Sum" }],
          ]
          yAxis = {
            left = { min = 0 }
          }
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 34
        width  = 8
        height = 6
        properties = {
          title  = "Errors"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", module.registration.lambda_name, { label = "registration", stat = "Sum" }],
            [".", ".", ".", module.deregistration.lambda_name, { label = "deregistration", stat = "Sum" }],
            [".", ".", ".", module.record_metric.lambda_name, { label = "record_metric", stat = "Sum" }],
          ]
          yAxis = {
            left = { min = 0 }
          }
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 34
        width  = 8
        height = 6
        properties = {
          title  = "Throttles"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["AWS/Lambda", "Throttles", "FunctionName", module.registration.lambda_name, { label = "registration", stat = "Sum" }],
            [".", ".", ".", module.deregistration.lambda_name, { label = "deregistration", stat = "Sum" }],
            [".", ".", ".", module.record_metric.lambda_name, { label = "record_metric", stat = "Sum" }],
          ]
          yAxis = {
            left = { min = 0 }
          }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 40
        width  = 24
        height = 6
        properties = {
          title  = "p95 duration"
          view   = "timeSeries"
          region = local.dashboard_region
          period = 60
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", module.registration.lambda_name, { label = "registration p95", stat = "p95" }],
            [".", ".", ".", module.deregistration.lambda_name, { label = "deregistration p95", stat = "p95" }],
            [".", ".", ".", module.record_metric.lambda_name, { label = "record_metric p95", stat = "p95" }],
          ]
          yAxis = {
            left = { min = 0, label = "ms" }
          }
        }
      },
    ],
  )
}

resource "aws_cloudwatch_dashboard" "actions_runner" {
  dashboard_name = aws_autoscaling_group.actions-runner.name

  dashboard_body = jsonencode({
    widgets = local.dashboard_widgets
  })
}
