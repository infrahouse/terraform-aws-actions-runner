variable "architecture" {
  description = "The CPU architecture for the Lambda function; valid values are `x86_64` or `arm64`."
  type        = string
  default     = "x86_64"
}

variable "alarm_emails" {
  description = "List of email addresses to receive alarm notifications for Lambda errors. At least one email is required for Lambda error monitoring."
  type        = list(string)
  validation {
    condition     = length(var.alarm_emails) > 0
    error_message = "At least one alarm email address must be provided for monitoring compliance"
  }
}

variable "asg_name" {
  description = "Autoscaling group name"
  type        = string
}

variable "cloudwatch_log_group_retention" {
  description = "Number of days you want to retain log events in the log group."
  default     = 365
  type        = number
}

variable "error_rate_threshold" {
  description = "Error rate threshold percentage for threshold-based alerting."
  type        = number
  default     = 10.0
  validation {
    condition     = var.error_rate_threshold > 0 && var.error_rate_threshold <= 100
    error_message = "error_rate_threshold must be between 0 and 100"
  }
}

variable "github_org_name" {
  description = "GitHub organization name."
  type        = string
}

variable "github_credentials" {
  description = "A secret and its type to auth in Github."
  type = object(
    {
      type : string   # Can be either "token" or "pem"
      secret : string # ARN where either is stored
    }
  )
}

variable "github_app_id" {
  description = "GitHub App that gives out GitHub tokens for Terraform. For instance, https://github.com/organizations/infrahouse/settings/apps/infrahouse-github-terraform"
  type        = string
}

variable "installation_id" {
  description = "Unique identifier of runners created by the action-runner module. Each runner has a label 'installation_id:<installation_id>'."
  type        = string
}

variable "lambda_timeout" {
  description = "Time in seconds to let lambda run."
  type        = number
  default     = 30
}

variable "registration_token_secret_prefix" {
  description = "Secret name prefix that will store a registration token"
  type        = string
}

variable "python_version" {
  description = "Python version to run lambda on. Must one of https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html"
  type        = string
  default     = "python3.12"
}

variable "security_group_ids" {
  description = "List of security group ids where the lambda will be created."
  type        = list(string)
}

variable "subnet_ids" {
  description = "List of subnet ids where the actions runner instances will be created."
  type        = list(string)
}

variable "tags" {
  description = "A map of tags to assign to resources."
  type        = map(string)
  default     = {}
}
