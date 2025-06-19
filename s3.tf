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

resource "aws_s3_bucket_policy" "lambda_tmp" {
  bucket = aws_s3_bucket.lambda_tmp.id
  policy = data.aws_iam_policy_document.bucket_policy.json
}

data "aws_iam_policy_document" "bucket_policy" {
  statement {
    sid    = "AllowSSLRequestsOnly"
    effect = "Deny"

    actions = [
      "s3:*",
    ]

    resources = [
      aws_s3_bucket.lambda_tmp.arn,
      "${aws_s3_bucket.lambda_tmp.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values = [
        "false"
      ]
    }
  }

}
