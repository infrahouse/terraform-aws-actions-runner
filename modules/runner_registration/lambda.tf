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
    actions = ["autoscaling:CompleteLifecycleAction"]
    resources = [
      local.asg_arn
    ]
  }
  statement {
    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeTags"
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
    resources = [
      data.aws_secretsmanager_secret.github_token.arn
    ]
  }
  statement {
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:PutSecretValue",
    ]
    resources = [
      "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:${var.registration_token_secret_prefix}-*"
    ]
  }
}

resource "aws_iam_policy" "lambda_logging" {
  name_prefix = "lambda_logging"
  description = "IAM policy for logging from a lambda"
  policy      = data.aws_iam_policy_document.lambda_logging.json
  tags        = var.tags
}

resource "aws_iam_policy" "lambda_permissions" {
  name_prefix = "lambda_permissions"
  description = "IAM policy for a lambda permissions"
  policy      = data.aws_iam_policy_document.lambda-permissions.json
  tags        = var.tags
}

resource "aws_iam_role" "lambda" {
  name_prefix        = "${var.github_org_name}-registration"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "AWSLambdaBasicExecutionRole" {
  role       = aws_iam_role.lambda.name
  policy_arn = data.aws_iam_policy.AWSLambdaBasicExecutionRole.arn
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = aws_iam_policy.lambda_logging.arn
}

resource "aws_iam_role_policy_attachment" "lambda_permissions" {
  policy_arn = aws_iam_policy.lambda_permissions.arn
  role       = aws_iam_role.lambda.name
}

resource "aws_lambda_function" "main" {
  s3_bucket     = aws_s3_bucket.lambda_tmp.bucket
  s3_key        = aws_s3_object.lambda_package.key
  function_name = "${var.asg_name}_registration"
  role          = aws_iam_role.lambda.arn
  handler       = "main.lambda_handler"

  runtime = "python3.12"
  timeout = 30
  environment {
    variables = {
      "GITHUB_ORG_NAME" : var.github_org_name,
      "GITHUB_TOKEN_SECRET" : var.github_token_secret,
      "REGISTRATION_TOKEN_SECRET_PREFIX" : var.registration_token_secret_prefix
    }
  }
  depends_on = [
    data.archive_file.lambda,
  ]
  tags = var.tags
}

resource "aws_lambda_function_event_invoke_config" "runner_registration" {
  function_name          = aws_lambda_function.main.function_name
  maximum_retry_attempts = 0
}

resource "aws_lambda_permission" "allow_cloudwatch_asg_lifecycle_hook" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.main.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.scale.arn
}
