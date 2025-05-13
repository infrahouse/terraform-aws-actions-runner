variable "asg_name" {
  description = "Autoscaling group name to assign this lambda to."
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

variable "lambda_bucket_name" {
  description = "S3 bucket to store lambda code"
  type        = string
}

variable "lambda_timeout" {
  description = "Time in seconds to let lambda run."
  default     = 900
}

variable "python_version" {
  default = "python3.12"
}

variable "registration_token_secret_prefix" {
  description = "Secret name prefix that will store a registration token"
}

variable "security_group_ids" {
  description = "List of security group ids where the lambda will be created."
  type        = list(string)
}

variable "subnet_ids" {
  description = "List of subnet ids where the actions runner instances will be created."
  type        = list(string)
}

variable "tags" {}
