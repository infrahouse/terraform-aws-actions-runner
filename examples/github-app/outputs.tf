output "autoscaling_group_name" {
  description = "Name of the runners' Auto Scaling Group."
  value       = module.actions_runner.autoscaling_group_name
}

output "runner_role_arn" {
  description = "ARN of the runner instance role. Grant it what your jobs need."
  value       = module.actions_runner.runner_role_arn
}

output "alarm_topic_arn" {
  description = "ARN of the SNS topic the pool's alarms go to. Subscribe more endpoints to it."
  value       = module.actions_runner.alarm_topic_arn
}

output "dashboard_url" {
  description = "URL of the pool's CloudWatch dashboard."
  value       = module.actions_runner.dashboard_url
}
