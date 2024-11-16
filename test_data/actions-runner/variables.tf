variable "region" {}
variable "test_zone" {}
variable "role_arn" {}
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


variable "subnet_public_ids" {}
variable "subnet_private_ids" {}
