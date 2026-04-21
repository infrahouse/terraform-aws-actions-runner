output "runner_role_arn" {
  description = "An actions runner EC2 instance role ARN."
  value       = module.instance-profile.instance_role_arn
}

output "autoscaling_group_name" {
  description = "Autoscaling group name."
  value       = aws_autoscaling_group.actions-runner.name
}

output "registration_token_secret_prefix" {
  description = "The prefix used for storing GitHub Actions runner registration token secrets in AWS Secrets Manager"
  value       = local.registration_token_secret_prefix
}

output "deregistration_log_group" {
  description = "CloudWatch log group name for the deregistration lambda"
  value       = module.deregistration.log_group_name
}

output "registration_lambda_name" {
  description = "Name of the runner_registration lambda function."
  value       = module.registration.lambda_name
}

output "deregistration_lambda_name" {
  description = "Name of the runner_deregistration lambda function."
  value       = module.deregistration.lambda_name
}

output "record_metric_lambda_name" {
  description = "Name of the record_metric lambda function."
  value       = module.record_metric.lambda_name
}

output "alarm_topic_arn" {
  description = "ARN of the SNS topic this module creates for alarm notifications. alarm_emails are subscribed to this topic; any ARNs passed in alarm_topic_arns receive the same alarms in addition to this one."
  value       = aws_sns_topic.alarms.arn
}

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard the module creates for this runner pool."
  value       = aws_cloudwatch_dashboard.actions_runner.dashboard_name
}

output "dashboard_url" {
  description = "URL of the CloudWatch dashboard the module creates for this runner pool."
  value       = "https://${data.aws_region.current.name}.console.aws.amazon.com/cloudwatch/home?region=${data.aws_region.current.name}#dashboards:name=${aws_cloudwatch_dashboard.actions_runner.dashboard_name}"
}
