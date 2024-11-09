module "jumphost" {
  source                   = "registry.infrahouse.com/infrahouse/jumphost/aws"
  version                  = "~> 2.10"
  environment              = var.environment
  keypair_name             = aws_key_pair.jumphost.key_name
  route53_zone_id          = data.aws_route53_zone.cicd.zone_id
  subnet_ids               = var.subnet_public_ids
  nlb_subnet_ids           = var.subnet_public_ids
  asg_min_size             = 1
  asg_max_size             = 1
  puppet_hiera_config_path = "/opt/infrahouse-puppet-data/environments/${var.environment}/hiera.yaml"
  packages = [
    "infrahouse-puppet-data"
  ]
}
