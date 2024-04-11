resource "aws_iam_policy" "required" {
  policy = data.aws_iam_policy_document.required_permissions.json
}

resource "random_string" "profile-suffix" {
  length  = 12
  special = false
}

module "instance-profile" {
  source       = "registry.infrahouse.com/infrahouse/instance-profile/aws"
  version      = "~> 1.3"
  permissions  = data.aws_iam_policy_document.required_permissions.json
  profile_name = "actions-runner-${random_string.profile-suffix.result}"
  extra_policies = merge(
    {
      required : aws_iam_policy.required.arn
    },
    var.extra_policies
  )
}

module "userdata" {
  source                   = "registry.infrahouse.com/infrahouse/cloud-init/aws"
  version                  = "~> 1.11"
  environment              = var.environment
  role                     = "gha_runner"
  puppet_debug_logging     = var.puppet_debug_logging
  puppet_environmentpath   = var.puppet_environmentpath
  puppet_hiera_config_path = var.puppet_hiera_config_path
  puppet_module_path       = var.puppet_module_path
  puppet_root_directory    = var.puppet_root_directory
  puppet_manifest          = var.puppet_manifest
  packages = concat(
    var.packages,
    [
      "make",
      "python-is-python3",
    ]
  )
  extra_files = var.extra_files
  extra_repos = var.extra_repos
  custom_facts = {
    labels : var.extra_labels
  }
}


resource "tls_private_key" "actions-runner" {
  algorithm = "RSA"
}

resource "aws_key_pair" "actions-runner" {
  key_name_prefix = "actions-runner-generated-"
  public_key      = tls_private_key.actions-runner.public_key_openssh
  tags            = local.tags
}

resource "aws_launch_template" "actions-runner" {
  name_prefix   = "actions-runner-"
  instance_type = var.instance_type
  key_name      = var.keypair_name == null ? aws_key_pair.actions-runner.key_name : var.keypair_name
  image_id      = var.ami_id == null ? data.aws_ami.ubuntu.id : var.ami_id
  iam_instance_profile {
    arn = module.instance-profile.instance_profile_arn
  }
  user_data = module.userdata.userdata
  vpc_security_group_ids = [
    aws_security_group.actions-runner.id
  ]
  tags = local.tags
}

resource "random_string" "asg_name" {
  length  = 6
  special = false
}

locals {
  asg_name = "${aws_launch_template.actions-runner.name}-${random_string.asg_name.result}"
}

resource "aws_autoscaling_group" "actions-runner" {
  name                  = local.asg_name
  min_size              = var.asg_min_size == null ? length(var.subnet_ids) : var.asg_min_size
  max_size              = var.asg_max_size == null ? length(var.subnet_ids) + 1 : var.asg_max_size
  vpc_zone_identifier   = var.subnet_ids
  max_instance_lifetime = 90 * 24 * 3600
  launch_template {
    id      = aws_launch_template.actions-runner.id
    version = aws_launch_template.actions-runner.latest_version
  }

  lifecycle {
    create_before_destroy = true
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 100
    }
  }

  tag {
    key                 = "Name"
    propagate_at_launch = true
    value               = "actions-runner"
  }

  dynamic "tag" {
    for_each = local.tags
    content {
      key                 = tag.key
      propagate_at_launch = true
      value               = tag.value
    }
  }
}

locals {
  lifecycle_hook_wait_time = 300
}

resource "aws_autoscaling_lifecycle_hook" "terminating" {
  name                   = "terminating"
  autoscaling_group_name = aws_autoscaling_group.actions-runner.name
  lifecycle_transition   = "autoscaling:EC2_INSTANCE_TERMINATING"
  heartbeat_timeout      = local.lifecycle_hook_wait_time
  default_result         = "ABANDON"
}
