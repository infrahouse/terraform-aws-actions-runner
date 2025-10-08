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
