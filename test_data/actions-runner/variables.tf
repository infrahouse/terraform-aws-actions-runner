variable "region" {}
variable "role_arn" {
  default = null
}
variable "github_token" {
  default = null
}
variable "github_app_pem_secret_arn" {
  default = null
}
variable "github_org_name" {}
variable "github_app_id" {
  default = null
}


variable "subnet_ids" {}
variable "lambda_subnet_ids" {}
variable "ubuntu_codename" {}

variable "architecture" {
  description = "The CPU architecture for the Lambda function; valid values are `x86_64` or `arm64`."
  type        = string
}

variable "python_version" {
}

variable "asg_max_size" {
}
