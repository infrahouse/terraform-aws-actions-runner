locals {
  lambda_root = "${path.module}/lambda"
}
resource "null_resource" "install_python_dependencies" {
  provisioner "local-exec" {
    command = "bash ${path.module}/package.sh"
    environment = {
      TARGET_DIR        = local.lambda_root
      MODULE_DIR        = path.module
      REQUIREMENTS_FILE = "${local.lambda_root}/requirements.txt"
    }
  }
  triggers = {
    dependencies_version = filemd5("${local.lambda_root}/requirements.txt")
    main_version         = filemd5("${local.lambda_root}/main.py")
  }
}

resource "random_uuid" "lamda_src_hash" {
  keepers = {
    for filename in setunion(
      fileset(local.lambda_root, "main.py"),
      fileset(local.lambda_root, "requirements.txt")
    ) :
    filename => filemd5("${local.lambda_root}/${filename}")
  }
}

data "archive_file" "lambda" {
  type = "zip"
  excludes = [
    "__pycache__"
  ]
  source_dir  = local.lambda_root
  output_path = "${path.module}/${random_uuid.lamda_src_hash.result}.zip"
  depends_on = [
    null_resource.install_python_dependencies
  ]
}

resource "aws_s3_object" "lambda_package" {
  bucket = var.lambda_bucket_name
  key    = basename(data.archive_file.lambda.output_path)
  source = data.archive_file.lambda.output_path
  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}
