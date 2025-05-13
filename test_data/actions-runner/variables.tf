variable "region" {}
variable "test_zone" {}
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
