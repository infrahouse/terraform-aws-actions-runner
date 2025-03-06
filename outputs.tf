output "runner_role_arn" {
  description = "An actions runner EC2 instance role ARN."
  value       = module.instance-profile.instance_role_arn
}

output "autoscaling_group_name" {
  description = "Autoscaling group name."
  value       = aws_autoscaling_group.actions-runner.name
}
