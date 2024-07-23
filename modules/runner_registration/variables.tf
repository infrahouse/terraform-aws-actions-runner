variable "asg_name" {
  description = "Autoscaling group name to assign this lambda to."
  type        = string
}

variable "github_org_name" {
  description = "GitHub organization name."
}

variable "github_token_secret" {
  description = "Secretsmanager secret name with the GitHub token."
}

variable "registration_token_secret_prefix" {
  description = "Secret name prefix that will store a registration token"
}
