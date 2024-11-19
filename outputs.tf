output "runner_role_arn" {
  description = "An actions runner EC2 instance role ARN."
  value = module.instance-profile.instance_role_arn
}
