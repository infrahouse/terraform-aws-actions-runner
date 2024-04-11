data "aws_iam_policy_document" "required_permissions" {
  statement {
    actions = [
      "autoscaling:DescribeAutoScalingInstances"
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
      data.aws_secretsmanager_secret.github_token.arn
    ]
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-${var.ubuntu_codename}-*"]
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

data "aws_secretsmanager_secret" "github_token" {
  arn = var.github_token_secret_arn
}
