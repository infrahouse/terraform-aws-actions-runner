locals {
  tags = {
    created_by_module : "infrahouse/actions-runner/aws"
  }
  ami_id = var.ami_id == null ? data.aws_ami.ubuntu.id : var.ami_id
}
