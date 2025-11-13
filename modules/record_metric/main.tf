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
  statement {
    actions = [
      "secretsmanager:GetSecretValue"
    ]
    resources = [var.github_credentials.secret]
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
  version = "1.0.3"

  function_name                 = "${var.asg_name}_record_metric"
  lambda_source_dir             = "${path.module}/lambda"
  architecture                  = var.architecture
  python_version                = var.python_version
  timeout                       = var.lambda_timeout
  memory_size                   = 128
  cloudwatch_log_retention_days = var.cloudwatch_log_group_retention
  alarm_emails                  = var.alarm_emails
  alert_strategy                = "threshold"
  error_rate_threshold          = var.error_rate_threshold
  additional_iam_policy_arns    = [aws_iam_policy.record_metric_permissions.arn]

  environment_variables = {
    ASG_NAME           = var.asg_name
    GITHUB_ORG_NAME    = var.github_org_name
    GITHUB_SECRET      = var.github_credentials.secret
    GITHUB_SECRET_TYPE = var.github_credentials.type
    GH_APP_ID          = var.github_app_id
    INSTALLATION_ID    = var.installation_id
  }

  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}
