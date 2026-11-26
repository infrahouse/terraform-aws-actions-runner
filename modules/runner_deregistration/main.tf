locals {
  # Needed for the IAM policy
  asg_arn = "arn:aws:autoscaling:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:autoScalingGroup:*:autoScalingGroupName/${var.asg_name}"

  # One error-rate period per scheduled sweep (issue #91). A period shorter than the gap between
  # sweeps holds at most one datapoint in the alarm's window, so the alarm could never collect two
  # breaching datapoints. The period must also be at least the Lambda's timeout, because a run's
  # datapoint arrives when the run ends (lambda-monitored issue #33). The timeout is at most 900 s,
  # which lambda-monitored validates, so keep the schedule at 15 minutes or more.
  error_rate_period = local.schedule_interval_minutes * 60

  # A KMS key's ARN goes into its own variable, so a kms Lambda is given no secret ARN.
  github_credential_environment = var.github_credentials.type == "kms" ? {
    GITHUB_APP_KMS_KEY_ARN = var.github_credentials.arn
    } : {
    GITHUB_SECRET = var.github_credentials.arn
  }
}

# Custom IAM policy for runner_deregistration lambda
data "aws_iam_policy_document" "runner_deregistration_permissions" {
  statement {
    actions = [
      "autoscaling:CompleteLifecycleAction",
    ]
    resources = [
      local.asg_arn
    ]
  }
  statement {
    # Describe actions require "*" resource
    actions = [
      "sts:GetCallerIdentity",
      "ec2:DescribeInstances",
      "ec2:DescribeTags",
      "ssm:GetCommandInvocation",
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:DescribeWarmPool",
    ]
    resources = ["*"]
  }
  statement {
    actions = [
      "ssm:SendCommand",
    ]
    resources = [
      "arn:aws:ec2:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:instance/*",
    ]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/aws:autoscaling:groupName"
      values   = [var.asg_name]
    }
  }
  statement {
    actions = [
      "ssm:SendCommand",
    ]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}::document/AWS-RunShellScript",
    ]
  }
  # The GitHub credential: read the token or PEM secret, or sign with the KMS key, never both (SEC-46).
  dynamic "statement" {
    for_each = var.github_credentials.type == "kms" ? [] : [var.github_credentials.arn]
    content {
      actions = [
        "secretsmanager:GetSecretValue"
      ]
      resources = [statement.value]
    }
  }
  dynamic "statement" {
    for_each = var.github_credentials.type == "kms" ? [var.github_credentials.arn] : []
    content {
      actions = [
        "kms:Sign"
      ]
      resources = [statement.value]
      # The App JWT is RS256, so the Lambda signs with this algorithm only.
      condition {
        test     = "StringEquals"
        variable = "kms:SigningAlgorithm"
        values   = ["RSASSA_PKCS1_V1_5_SHA_256"]
      }
    }
  }
  statement {
    actions = [
      "secretsmanager:DeleteSecret",
      "secretsmanager:DescribeSecret"
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

resource "aws_iam_policy" "runner_deregistration_permissions" {
  name_prefix = "${var.asg_name}-runner-deregistration-"
  description = "IAM policy for runner_deregistration lambda permissions"
  policy      = data.aws_iam_policy_document.runner_deregistration_permissions.json
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
  version = "2.2.0"

  function_name                        = "${var.asg_name}_deregistration"
  lambda_source_dir                    = "${path.module}/lambda"
  architecture                         = var.architecture
  python_version                       = var.python_version
  timeout                              = var.lambda_timeout
  memory_size                          = 512
  memory_utilization_threshold_percent = 80
  cloudwatch_log_retention_days        = var.cloudwatch_log_group_retention
  alarm_emails                         = var.alarm_emails
  additional_iam_policy_arns           = [aws_iam_policy.runner_deregistration_permissions.arn]
  role_name_prefix                     = var.role_name_prefix

  # Error alarm: fires when two consecutive periods breach error_rate_threshold, which takes about
  # an hour of failing sweeps. One failed sweep doesn't alarm.
  alert_strategy                 = "threshold"
  error_rate_threshold           = var.error_rate_threshold
  error_rate_period              = local.error_rate_period
  error_rate_evaluation_periods  = 2
  error_rate_datapoints_to_alarm = 2

  # VPC Configuration
  lambda_subnet_ids         = var.subnet_ids
  lambda_security_group_ids = var.security_group_ids

  environment_variables = merge(
    {
      ASG_NAME                         = var.asg_name
      REGISTRATION_TOKEN_SECRET_PREFIX = var.registration_token_secret_prefix
      GITHUB_ORG_NAME                  = var.github_org_name
      GITHUB_SECRET_TYPE               = var.github_credentials.type
      GH_APP_ID                        = var.github_app_id
      INSTALLATION_ID                  = var.installation_id
    },
    local.github_credential_environment,
  )

  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}
