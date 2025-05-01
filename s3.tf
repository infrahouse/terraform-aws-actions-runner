resource "aws_s3_bucket" "lambda_tmp" {
  bucket_prefix = "infrahouse-action-runner-lambda-"
  tags          = local.default_module_tags
}

resource "aws_s3_bucket_public_access_block" "public_access" {
  bucket                  = aws_s3_bucket.lambda_tmp.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

