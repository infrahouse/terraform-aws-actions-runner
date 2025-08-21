locals {
  asg_arn = "arn:aws:autoscaling:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.asg_name}"
  norm_arch = contains(["arm64", "aarch64"], var.architecture) ? "aarch64" : (
    contains(["x86_64", "amd64"], var.architecture) ? "x86_64" : var.architecture
  )

}
