# State migration blocks for adopting terraform-aws-lambda-monitored module
# These blocks ensure smooth upgrades from version 3.0.x to 3.1.x without losing log data

# CloudWatch Log Group - MUST be moved to preserve existing log data
moved {
  from = aws_cloudwatch_log_group.lambda
  to   = module.lambda_monitored.aws_cloudwatch_log_group.lambda
}

# Note: All other resources (IAM roles, Lambda function, policies, etc.) will be
# destroyed and recreated during the migration. This is acceptable as they don't
# contain any persistent data that would be lost.