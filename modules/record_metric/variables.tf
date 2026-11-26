variable "architecture" {
  description = "The CPU architecture for the Lambda function; valid values are `x86_64` or `arm64`."
  type        = string
  default     = "x86_64"
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

variable "github_org_name" {
  description = "GitHub organization name."
  type        = string
}

variable "github_credentials" {
  description = <<-EOT
    How the Lambda authenticates to GitHub. type "token": arn is a Secrets Manager secret with a classic GitHub token.
    type "pem": arn is a secret with the GitHub App's private key. type "kms": arn is a KMS key with the App's
    private key; the Lambda signs the App JWT with kms:Sign and can't read the key.
  EOT
  type = object(
    {
      type : string
      arn : string
    }
  )
  validation {
    condition     = contains(["token", "pem", "kms"], var.github_credentials.type)
    error_message = "github_credentials.type must be token, pem or kms. Got: ${jsonencode(var.github_credentials.type)}"
  }
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

variable "python_version" {
  description = <<-EOT
    Python runtime for the Lambda function: python3.12 or python3.13. terraform-aws-lambda-monitored
    validates it, because only the Amazon Linux 2023 runtimes can load the manylinux_2_28 wheels it installs.
  EOT
  type        = string
  default     = "python3.12"
}

variable "role_name_prefix" {
  description = <<-EOT
    Name prefix for the Lambda execution role, passed to terraform-aws-lambda-monitored as role_name_prefix.
    It is used verbatim and Terraform appends a 26-character suffix, so end it with "-".
    1 to 38 characters from letters, digits and +=,.@_-.
  EOT
  type        = string
}

variable "tags" {
  description = "A map of tags to assign to resources."
  type        = map(string)
  default     = {}
}

variable "alarm_emails" {
  description = "List of email addresses to receive alarm notifications for Lambda errors. At least one email is required for Lambda error monitoring."
  type        = list(string)
  validation {
    condition     = length(var.alarm_emails) > 0
    error_message = "At least one alarm email address must be provided for monitoring compliance"
  }
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
