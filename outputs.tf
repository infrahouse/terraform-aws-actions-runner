output "runner_role_arn" {
  description = "ARN of the runner instance role, the role the runner EC2 instances use."
  value       = module.instance-profile.instance_role_arn
}

output "runner_role_name" {
  description = <<-EOT
    Name of the runner instance role: actions-runner-instance-<pool>, with the same <pool> as
    lambda_role_name_prefix. It never matches actions-runner-lambda-*, the prefix of the module's Lambda roles.
  EOT
  value       = module.instance-profile.instance_role_name
}

output "lambda_role_name_prefix" {
  description = <<-EOT
    Name prefix shared by this pool's three Lambda execution roles: actions-runner-lambda-<pool>-.
    Each role is named <prefix><function>-<suffix>, where <function> is reg, dereg or metric.
    <pool> stays the same for the life of the pool. Every pool's Lambda roles match actions-runner-lambda-*.
  EOT
  value       = local.lambda_role_name_prefix
}

output "lambda_role_arn_pattern" {
  description = <<-EOT
    ARN pattern that matches this pool's three Lambda execution roles and none of its other roles,
    e.g. for an ArnLike condition on aws:PrincipalArn:
    arn:<partition>:iam::<account>:role/actions-runner-lambda-<pool>-*
  EOT
  value = join("", [
    "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/",
    "${local.lambda_role_name_prefix}*",
  ])
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
