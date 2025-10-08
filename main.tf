resource "aws_iam_policy" "required" {
  policy = data.aws_iam_policy_document.required_permissions.json
}

resource "random_string" "profile-suffix" {
  length  = 12
  special = false
}

resource "random_uuid" "installation-id" {
}

module "instance-profile" {
  source       = "registry.infrahouse.com/infrahouse/instance-profile/aws"
  version      = "1.9.0"
  permissions  = data.aws_iam_policy_document.required_permissions.json
  profile_name = "actions-runner-${random_string.profile-suffix.result}"
  role_name    = var.role_name
  extra_policies = merge(
    {
      ssm : data.aws_iam_policy.ssm.arn
    },
    var.extra_policies
  )
}

module "userdata" {
  source  = "registry.infrahouse.com/infrahouse/cloud-init/aws"
  version = "2.2.2"

  environment              = var.environment
  role                     = "gha_runner"
  puppet_debug_logging     = var.puppet_debug_logging
  puppet_environmentpath   = var.puppet_environmentpath
  puppet_hiera_config_path = var.puppet_hiera_config_path
  puppet_module_path       = var.puppet_module_path
  puppet_root_directory    = var.puppet_root_directory
  puppet_manifest          = var.puppet_manifest
  ubuntu_codename          = var.ubuntu_codename
  packages = concat(
    var.packages,
    [
      "gh",
      "make",
      "python-is-python3",
    ]
  )
  extra_files = var.extra_files
  extra_repos = var.extra_repos
  custom_facts = {
    labels : concat(
      [
        "aws_region:${data.aws_region.current.name}",
        "aws_account:${data.aws_caller_identity.current.account_id}",
        "installation_id:${random_uuid.installation-id.result}",
      ],
      var.extra_labels
    )
    registration_token_secret_prefix : local.registration_token_secret_prefix
    bootstrap_hookname : local.bootstrap_hookname
  }
  post_runcmd = concat(
    var.post_runcmd,
    [
      "ih-aws --verbose autoscaling complete ${local.bootstrap_hookname}"
    ]
  )
}


resource "tls_private_key" "actions-runner" {
  algorithm = "RSA"
}

resource "aws_key_pair" "actions-runner" {
  key_name_prefix = "actions-runner-generated-"
  public_key      = tls_private_key.actions-runner.public_key_openssh
  tags            = local.default_module_tags
}

resource "aws_launch_template" "actions-runner" {
  name_prefix   = "actions-runner-"
  instance_type = var.instance_type
  key_name      = var.keypair_name == null ? aws_key_pair.actions-runner.key_name : var.keypair_name
  image_id      = local.ami_id
  iam_instance_profile {
    arn = module.instance-profile.instance_profile_arn
  }
  block_device_mappings {
    device_name = data.aws_ami.selected.root_device_name
    ebs {
      volume_size           = var.root_volume_size
      delete_on_termination = true
      encrypted             = true
    }
  }
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }
  user_data = module.userdata.userdata
  vpc_security_group_ids = [
    aws_security_group.actions-runner.id
  ]
  tags = local.default_module_tags
  tag_specifications {
    resource_type = "volume"
    tags = merge(
      data.aws_default_tags.provider.tags,
      local.default_module_tags
    )
  }
  tag_specifications {
    resource_type = "network-interface"
    tags = merge(
      data.aws_default_tags.provider.tags,
      local.default_module_tags
    )
  }

}

resource "random_string" "asg_name" {
  length  = 6
  special = false
}

resource "random_string" "reg_token_suffix" {
  length  = 6
  special = false
}

locals {
  asg_name = "${aws_launch_template.actions-runner.name}-${random_string.asg_name.result}"
}

resource "aws_autoscaling_group" "actions-runner" {
  name                      = local.asg_name
  min_size                  = var.asg_min_size == null ? length(var.subnet_ids) : var.asg_min_size
  max_size                  = var.asg_max_size == null ? length(var.subnet_ids) + 1 : var.asg_max_size
  vpc_zone_identifier       = var.subnet_ids
  max_instance_lifetime     = var.max_instance_lifetime_days * 24 * 3600
  health_check_grace_period = 0
  wait_for_capacity_timeout = "15m"
  dynamic "launch_template" {
    for_each = var.on_demand_base_capacity == null ? [1] : []
    content {
      id      = aws_launch_template.actions-runner.id
      version = aws_launch_template.actions-runner.latest_version
    }
  }

  dynamic "mixed_instances_policy" {
    for_each = var.on_demand_base_capacity == null ? [] : [1]
    content {
      instances_distribution {
        on_demand_base_capacity                  = var.on_demand_base_capacity
        on_demand_percentage_above_base_capacity = 0
      }
      launch_template {
        launch_template_specification {
          launch_template_id = aws_launch_template.actions-runner.id
          version            = aws_launch_template.actions-runner.latest_version
        }
      }
    }
  }

  initial_lifecycle_hook {
    lifecycle_transition = "autoscaling:EC2_INSTANCE_LAUNCHING"
    name                 = local.registration_hookname
    heartbeat_timeout    = 300
    default_result       = "ABANDON"
  }

  initial_lifecycle_hook {
    lifecycle_transition = "autoscaling:EC2_INSTANCE_LAUNCHING"
    name                 = local.bootstrap_hookname
    heartbeat_timeout    = 1200
    default_result       = "ABANDON"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 100
    }
  }

  dynamic "warm_pool" {
    for_each = var.on_demand_base_capacity == null ? [1] : []
    content {
      pool_state                  = "Hibernated"
      min_size                    = var.warm_pool_min_size != null ? var.warm_pool_min_size : var.idle_runners_target_count + 1
      max_group_prepared_capacity = var.warm_pool_max_size != null ? var.warm_pool_max_size : var.asg_max_size
      instance_reuse_policy {
        reuse_on_scale_in = false
      }
    }
  }
  tag {
    key                 = "Name"
    propagate_at_launch = true
    value               = "actions-runner"
  }

  tag {
    key                 = "lambda_name"
    propagate_at_launch = true
    value               = module.registration.lambda_name
  }

  tag {
    key                 = "module_version"
    propagate_at_launch = true
    value               = local.module_version
  }

  dynamic "tag" {
    for_each = merge(
      local.default_module_tags,
      data.aws_default_tags.provider.tags
    )
    content {
      key                 = tag.key
      propagate_at_launch = true
      value               = tag.value
    }
  }
  depends_on = [
    module.instance-profile
  ]
  timeouts {
    delete = "30m"
  }
}


resource "aws_autoscaling_lifecycle_hook" "terminating" {
  name                   = local.deregistration_hookname
  autoscaling_group_name = aws_autoscaling_group.actions-runner.name
  lifecycle_transition   = "autoscaling:EC2_INSTANCE_TERMINATING"
  heartbeat_timeout      = 3600
  default_result         = "ABANDON"
}
