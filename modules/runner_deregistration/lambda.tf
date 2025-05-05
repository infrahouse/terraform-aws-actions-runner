data "aws_iam_policy_document" "assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

data "aws_iam_policy" "AWSLambdaBasicExecutionRole" {
  name = "AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_logging" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
    ]
    resources = [
      aws_cloudwatch_log_group.lambda.arn
    ]
  }
  statement {
    effect = "Allow"
    actions = [
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:*:log-stream:*"
    ]
  }
}

data "aws_iam_policy_document" "lambda-permissions" {
  statement {
    actions = [
      "sts:GetCallerIdentity",
      "autoscaling:CompleteLifecycleAction"
    ]
    resources = [
      "*"
    ]
  }
  statement {
    actions = [
      "autoscaling:DescribeAutoScalingGroups",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:DescribeWarmPool",
    ]
    resources = [
      "*"
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
      "secretsmanager:DeleteSecret"
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

resource "aws_iam_policy" "lambda_logging" {
  name_prefix = "lambda_logging"
  description = "IAM policy for logging from a lambda ${aws_lambda_function.lambda.function_name}"
  policy      = data.aws_iam_policy_document.lambda_logging.json
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}

resource "aws_iam_policy" "lambda_permissions" {
  name_prefix = "lambda_permissions"
  description = "IAM policy for a lambda permissions"
  policy      = data.aws_iam_policy_document.lambda-permissions.json
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}

resource "aws_iam_role" "iam_for_lambda" {
  name_prefix        = "${var.github_org_name}-registration"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}

resource "aws_iam_role_policy_attachment" "AWSLambdaBasicExecutionRole" {
  role       = aws_iam_role.iam_for_lambda.name
  policy_arn = data.aws_iam_policy.AWSLambdaBasicExecutionRole.arn
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.iam_for_lambda.name
  policy_arn = aws_iam_policy.lambda_logging.arn
}

resource "aws_iam_role_policy_attachment" "lambda_permissions" {
  policy_arn = aws_iam_policy.lambda_permissions.arn
  role       = aws_iam_role.iam_for_lambda.name
}

resource "aws_lambda_function" "lambda" {
  s3_bucket     = var.lambda_bucket_name
  s3_key        = aws_s3_object.lambda_package.key
  function_name = "${var.asg_name}_deregistration"
  role          = aws_iam_role.iam_for_lambda.arn
  handler       = "main.lambda_handler"

  runtime = var.python_version
  timeout = var.lambda_timeout
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
  environment {
    variables = {
      "ASG_NAME" : var.asg_name
      "HOOK_NAME" : var.hook_name
      "REGISTRATION_TOKEN_SECRET_PREFIX" : var.registration_token_secret_prefix

      "GITHUB_ORG_NAME" : var.github_org_name,
      "GITHUB_SECRET" : var.github_credentials.secret,
      "GITHUB_SECRET_TYPE" : var.github_credentials.type,
      "GH_APP_ID" : var.github_app_id
    }
  }
  depends_on = [
    data.archive_file.lambda,
    aws_s3_object.lambda_package,
  ]
}

resource "aws_lambda_function_event_invoke_config" "update_filter" {
  function_name          = aws_lambda_function.lambda.function_name
  maximum_retry_attempts = 0
}
