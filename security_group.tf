resource "aws_security_group" "actions-runner" {
  vpc_id      = data.aws_subnet.selected.vpc_id
  name_prefix = "postfix"
  description = "Manage traffic to GitHub Actions runner"
  tags = merge(
    {
      Name : "actions-runner"
    },
    local.default_module_tags
  )
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  description       = "Allow SSH traffic"
  security_group_id = aws_security_group.actions-runner.id
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
  cidr_ipv4         = data.aws_vpc.selected.cidr_block
  tags = merge({
    Name = "SSH access"
    },
    local.default_module_tags
  )
}

resource "aws_vpc_security_group_ingress_rule" "icmp" {
  description       = "Allow all ICMP traffic"
  security_group_id = aws_security_group.actions-runner.id
  from_port         = -1
  to_port           = -1
  ip_protocol       = "icmp"
  cidr_ipv4         = "0.0.0.0/0"
  tags = merge({
    Name = "ICMP traffic"
    },
    local.default_module_tags
  )
}

resource "aws_vpc_security_group_egress_rule" "default" {
  description       = "Allow all traffic"
  security_group_id = aws_security_group.actions-runner.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  tags = merge(
    {
      Name = "outgoing traffic"
    },
    local.default_module_tags
  )
}
