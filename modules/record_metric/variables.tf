variable "asg_name" {
  description = "Autoscaling group name"
  type        = string
}

variable "asg_arn" {
  description = "Autoscaling group ARN"
  type        = string
}

variable "cloudwatch_log_group_retention" {
  description = "Number of days you want to retain log events in the log group."
  default     = 365
  type        = number
}

variable "github_org_name" {
  description = "GitHub organization name."
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
}

variable "installation_id" {
  description = "Unique identifier of runners created by the action-runner module. Each runner has a label 'installation_id:<installation_id>'."
  type        = string
}

variable "lambda_bucket_name" {
  description = "S3 bucket to store lambda code"
  type        = string
}

variable "lambda_timeout" {
  description = "Time in seconds to let lambda run."
  default     = 30
}

variable "python_version" {
  description = "Python version to run lambda on. Must one of https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html"
  default     = "python3.12"
}

variable "tags" {}
