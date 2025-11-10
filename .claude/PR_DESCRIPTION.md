# PR Title

```
Migrate record_metric Lambda to terraform-aws-lambda-monitored module
```

# PR Description

## Summary

This PR migrates the `record_metric` Lambda function (which tracks GitHub Actions runner metrics) from custom packaging scripts to the standardized `terraform-aws-lambda-monitored` module. This migration significantly reduces boilerplate code (~200 lines removed), adds built-in error monitoring, and improves maintainability.

## What Changed

### Replaced Custom Implementation
The `modules/record_metric` module previously managed Lambda packaging manually through:
- Custom `package.sh` script for dependency installation
- Manual S3 bucket management via `lambda_bucket_name` variable
- Custom IAM role and CloudWatch log group setup
- Manual archiving and upload logic

### Now Uses Standardized Module
The module now leverages `terraform-aws-lambda-monitored` v0.3.4 which provides:
- ✅ Automated Python dependency packaging (no more custom scripts)
- ✅ Built-in error monitoring and alerting via SNS
- ✅ Standardized CloudWatch integration with configurable error thresholds
- ✅ Intelligent build artifact management (survives `terraform get -update`)
- ✅ Consistent IAM role structure across all lambdas

### Files Removed
- `modules/record_metric/lambda.tf` (164 lines)
- `modules/record_metric/lambda_code.tf` (73 lines)
- `modules/record_metric/cloudwatch.tf` (10 lines)
- `modules/record_metric/package.sh` (76 lines)
- `modules/record_metric/locals.tf` (5 lines)

**Total:** ~328 lines of boilerplate code removed

### Files Modified
- `modules/record_metric/main.tf` - Now uses the monitored module
- `modules/record_metric/eventbridge.tf` - Updated to reference module outputs
- `modules/record_metric/terraform.tf` - Removed null and random providers
- `modules/record_metric/variables.tf` - Added alarm_emails, error_rate_threshold
- `modules/record_metric/outputs.tf` - Updated to use module outputs
- `cloudwatch.tf` - Updated parent module call with new variables
- `variables.tf` - Added new variables, removed lambda_bucket_name
- `README.md` - Updated documentation

### New Documentation
- `.claude/architecture-notes.md` - Explains the lambda packaging architecture and how the module solves the build directory persistence problem

## Breaking Changes

⚠️ **Users must update their module calls when upgrading**

### Removed Variable: `lambda_bucket_name`
The `terraform-aws-lambda-monitored` module creates its own S3 bucket for Lambda packages, so the `lambda_bucket_name` variable has been removed.

**Migration:**
```diff
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
- version = "2.19.0"
+ version = "2.20.0"  # (or latest)

  # ... other variables ...

- lambda_bucket_name = aws_s3_bucket.lambda.bucket  # REMOVE THIS LINE
+ alarm_emails       = ["devops@example.com"]       # ADD THIS (required)
}
```

### New Required Variable: `alarm_emails`
Lambda error monitoring now requires at least one email address for ISO 27001 compliance.

**Type:** `list(string)`
**Example:** `["devops@example.com", "oncall@example.com"]`

### New Optional Variable: `error_rate_threshold`
Configure the error rate percentage threshold for alerting.

**Type:** `number`
**Default:** `10.0`
**Range:** 0-100
**Example:** `error_rate_threshold = 5.0` (alert at 5% error rate)

## Benefits

1. **Reduced Maintenance Burden** - 328 lines of custom code replaced with a maintained module
2. **Built-in Monitoring** - Automatic error alerting via SNS with configurable thresholds
3. **Better CI/CD Support** - Build artifacts persist correctly with `terraform get -update`
4. **Consistent Architecture** - Same lambda packaging approach across all functions
5. **Improved Change Detection** - Hash-based filenames provide deterministic builds
6. **Future-Proof** - Module updates will bring improvements to all users

## Testing

✅ Full integration test passed successfully:
- **Test:** `test_module[token-noble-aws-6]`
- **Duration:** 57 minutes (3,423 seconds)
- **Resources:** 35 resources created and destroyed successfully
- **Lambda Deployment:** `module.record_metric.module.lambda_monitored` deployed and executed correctly
- **Metrics Collection:** Lambda successfully published IdleRunners and BusyRunners metrics
- **Results:** See `pytest-20251110-081439-output.log`

## Architecture Details

For a deep dive into how the lambda packaging works and why this approach solves the build directory persistence problem, see:

📖 **[.claude/architecture-notes.md](./.claude/architecture-notes.md)**

Key insights:
- Why `path.root` instead of `path.module` for build artifacts
- How hash-based filenames provide deterministic builds
- Why the module creates its own S3 bucket

## Upgrade Path

1. **Remove** the `lambda_bucket_name` parameter from your module call
2. **Add** the `alarm_emails` parameter with at least one email address
3. **Optional:** Add `error_rate_threshold` if you want non-default alerting
4. Run `terraform plan` to review changes
5. Run `terraform apply` to complete the migration

### Example Before/After

**Before (v2.19.0):**
```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "2.19.0"

  asg_min_size        = 1
  asg_max_size        = 3
  subnet_ids          = var.subnet_private_ids
  environment         = "production"
  github_org_name     = "my-org"
  github_token_secret_arn = var.github_token_arn

  lambda_bucket_name = aws_s3_bucket.lambda.bucket
}
```

**After (v2.20.0):**
```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "2.20.0"

  asg_min_size        = 1
  asg_max_size        = 3
  subnet_ids          = var.subnet_private_ids
  environment         = "production"
  github_org_name     = "my-org"
  github_token_secret_arn = var.github_token_arn

  # New required variable
  alarm_emails = ["devops@my-org.com"]

  # Optional: customize error threshold (defaults to 10.0)
  error_rate_threshold = 5.0
}
```

## Related

- Module: [terraform-aws-lambda-monitored](https://registry.terraform.io/modules/infrahouse/lambda-monitored/aws/0.3.4)
- Architecture docs: [.claude/architecture-notes.md](./.claude/architecture-notes.md)

## Checklist

- ✅ Code changes implemented
- ✅ Tests passing (full integration test)
- ✅ Documentation updated (README.md)
- ✅ Breaking changes documented
- ✅ Architecture notes added
- ✅ Variables updated (removed lambda_bucket_name, added alarm_emails/error_rate_threshold)
- ✅ Example usage updated