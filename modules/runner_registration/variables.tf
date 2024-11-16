variable "asg_name" {
  description = "Autoscaling group name to assign this lambda to."
  type        = string
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

variable "lambda_timeout" {
  description = "Time in seconds to let lambda run."
  default     = 30
}
variable "registration_token_secret_prefix" {
  description = "Secret name prefix that will store a registration token"
}

variable "tags" {}
