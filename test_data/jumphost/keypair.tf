resource "tls_private_key" "rsa" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "jumphost" {
  public_key = tls_private_key.rsa.public_key_openssh
}
