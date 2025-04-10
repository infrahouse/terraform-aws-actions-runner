data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_default_tags" "provider" {}

data "aws_iam_policy_document" "required_permissions" {
  source_policy_documents = [var.extra_instance_profile_permissions]
  statement {
    actions = [
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:SetInstanceHealth"
    ]
    resources = [
      aws_autoscaling_group.actions-runner.arn
    ]
  }
  statement {
    actions = [
      "secretsmanager:GetSecretValue"
    ]
    resources = [
      join(
        ":",
        [
          "arn",
          "aws",
          "secretsmanager",
          data.aws_region.current.name,
          data.aws_caller_identity.current.account_id,
          "secret",
          "${local.registration_token_secret_prefix}-*"
        ]
      )
    ]
  }
}

locals {
  ami_name_pattern = contains(
    ["focal", "jammy"], var.ubuntu_codename
  ) ? "ubuntu/images/hvm-ssd/ubuntu-${var.ubuntu_codename}-*" : "ubuntu/images/hvm-ssd-gp3/ubuntu-${var.ubuntu_codename}-*"
}
data "aws_ami" "ubuntu" {
  most_recent = true

  filter {
    name   = "name"
    values = [local.ami_name_pattern]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name = "state"
    values = [
      "available"
    ]
  }

  owners = ["099720109477"] # Canonical
}

data "aws_subnet" "selected" {
  id = var.subnet_ids[0]
}

data "aws_vpc" "selected" {
  id = data.aws_subnet.selected.vpc_id
}

data "aws_ami" "selected" {
  filter {
    name = "image-id"
    values = [
      local.ami_id
    ]
  }
}
