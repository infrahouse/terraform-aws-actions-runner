# Local values needed for IAM policy
locals {
  asg_arn = "arn:aws:autoscaling:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.asg_name}"
}

# Custom IAM policy for runner_registration lambda
data "aws_iam_policy_document" "runner_registration_permissions" {
  statement {
    actions = [
      "autoscaling:CompleteLifecycleAction",
      "autoscaling:RecordLifecycleActionHeartbeat",
    ]
    resources = [
      local.asg_arn
    ]
  }
  statement {
    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeTags",
      "ssm:SendCommand",
      "ssm:GetCommandInvocation",
    ]
    resources = ["*"]
  }
  statement {
    actions = [
      "ec2:CreateTags",
    ]
    resources = [
      "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:instance/*"
    ]
  }
  statement {
    actions = [
      "secretsmanager:GetSecretValue"
    ]
    resources = [var.github_credentials.secret]
  }
  statement {
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:DescribeSecret",
      "secretsmanager:PutSecretValue",
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
          "${var.registration_token_secret_prefix}-*"
        ]
      )
    ]
  }
}

resource "aws_iam_policy" "runner_registration_permissions" {
  name_prefix = "${var.asg_name}-runner-registration-"
  description = "IAM policy for runner_registration lambda permissions"
  policy      = data.aws_iam_policy_document.runner_registration_permissions.json
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}

# Lambda function with monitoring using terraform-aws-lambda-monitored module
module "lambda_monitored" {
  source  = "registry.infrahouse.com/infrahouse/lambda-monitored/aws"
  version = "1.0.4"

  function_name                 = "${var.asg_name}_registration"
  lambda_source_dir             = "${path.module}/lambda"
  architecture                  = var.architecture
  python_version                = var.python_version
  timeout                       = var.lambda_timeout
  memory_size                   = 128
  cloudwatch_log_retention_days = var.cloudwatch_log_group_retention
  alarm_emails                  = var.alarm_emails
  alert_strategy                = "threshold"
  error_rate_threshold          = var.error_rate_threshold
  additional_iam_policy_arns    = [aws_iam_policy.runner_registration_permissions.arn]

  # VPC Configuration
  lambda_subnet_ids         = var.subnet_ids
  lambda_security_group_ids = var.security_group_ids

  environment_variables = {
    GITHUB_ORG_NAME                  = var.github_org_name
    GITHUB_SECRET                    = var.github_credentials.secret
    GITHUB_SECRET_TYPE               = var.github_credentials.type
    GH_APP_ID                        = var.github_app_id
    REGISTRATION_TOKEN_SECRET_PREFIX = var.registration_token_secret_prefix
    LAMBDA_TIMEOUT                   = var.lambda_timeout
  }

  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}
