data "aws_route53_zone" "cicd" {
  name = var.test_zone
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "external" "env" {
  program = ["bash", "${path.module}/env.sh"]
}
