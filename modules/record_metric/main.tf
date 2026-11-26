locals {
  # A KMS key's ARN goes into its own variable, so a kms Lambda is given no secret ARN.
  github_credential_environment = var.github_credentials.type == "kms" ? {
    GITHUB_APP_KMS_KEY_ARN = var.github_credentials.arn
    } : {
    GITHUB_SECRET = var.github_credentials.arn
  }
}

# Custom IAM policy for record_metric lambda
data "aws_iam_policy_document" "record_metric_permissions" {
  statement {
    actions = [
      "sts:GetCallerIdentity",
    ]
    resources = [
      "*"
    ]
  }
  statement {
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
    ]
    resources = [
      "*"
    ]
  }
  statement {
    actions = [
      "cloudwatch:PutMetricData"
    ]
    resources = [
      "*"
    ]
    condition {
      test = "StringEquals"
      values = [
        "GitHubRunners"
      ]
      variable = "cloudwatch:namespace"
    }
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
}

resource "aws_iam_policy" "record_metric_permissions" {
  name_prefix = "${var.asg_name}-record-metric-"
  description = "IAM policy for record_metric lambda permissions"
  policy      = data.aws_iam_policy_document.record_metric_permissions.json
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

  function_name                        = "${var.asg_name}_record_metric"
  lambda_source_dir                    = "${path.module}/lambda"
  architecture                         = var.architecture
  python_version                       = var.python_version
  timeout                              = var.lambda_timeout
  memory_size                          = 512
  memory_utilization_threshold_percent = 80
  cloudwatch_log_retention_days        = var.cloudwatch_log_group_retention
  alarm_emails                         = var.alarm_emails
  alert_strategy                       = "threshold"
  error_rate_threshold                 = var.error_rate_threshold
  additional_iam_policy_arns           = [aws_iam_policy.record_metric_permissions.arn]
  role_name_prefix                     = var.role_name_prefix

  environment_variables = merge(
    {
      ASG_NAME           = var.asg_name
      GITHUB_ORG_NAME    = var.github_org_name
      GITHUB_SECRET_TYPE = var.github_credentials.type
      GH_APP_ID          = var.github_app_id
      INSTALLATION_ID    = var.installation_id
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
