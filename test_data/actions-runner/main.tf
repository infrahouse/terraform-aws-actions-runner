locals {
  environment = "development"
}
resource "aws_key_pair" "test" {
  public_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDpgAP1z1Lxg9Uv4tam6WdJBcAftZR4ik7RsSr6aNXqfnTj4civrhd/q8qMqF6wL//3OujVDZfhJcffTzPS2XYhUxh/rRVOB3xcqwETppdykD0XZpkHkc8XtmHpiqk6E9iBI4mDwYcDqEg3/vrDAGYYsnFwWmdDinxzMH1Gei+NPTmTqU+wJ1JZvkw3WBEMZKlUVJC/+nuv+jbMmCtm7sIM4rlp2wyzLWYoidRNMK97sG8+v+mDQol/qXK3Fuetj+1f+vSx2obSzpTxL4RYg1kS6W1fBlSvstDV5bQG4HvywzN5Y8eCpwzHLZ1tYtTycZEApFdy+MSfws5vPOpggQlWfZ4vA8ujfWAF75J+WABV4DlSJ3Ng6rLMW78hVatANUnb9s4clOS8H6yAjv+bU3OElKBkQ10wNneoFIMOA3grjPvPp5r8dI0WDXPIznJThDJO5yMCy3OfCXlu38VDQa1sjVj1zAPG+Vn2DsdVrl50hWSYSB17Zww0MYEr8N5rfFE= aleks@MediaPC"
}

module "actions-runner" {
  source                    = "../.."
  asg_min_size              = 1
  asg_max_size              = 5
  subnet_ids                = var.subnet_ids
  environment               = local.environment
  github_org_name           = var.github_org_name
  github_app_pem_secret_arn = var.github_app_pem_secret_arn
  github_token_secret_arn   = var.github_token != null ? aws_secretsmanager_secret.github_token.arn : null
  puppet_hiera_config_path  = "/opt/infrahouse-puppet-data/environments/${local.environment}/hiera.yaml"
  keypair_name              = aws_key_pair.test.key_name
  packages = [
    "infrahouse-puppet-data"
  ]
  extra_labels    = ["awesome"]
  github_app_id   = var.github_app_id
  ubuntu_codename = var.ubuntu_codename
}
