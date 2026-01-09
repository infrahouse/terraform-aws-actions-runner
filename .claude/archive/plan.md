● Perfect! I now have a complete understanding of the current implementation. Let me prepare a comprehensive migration plan for you.

Current modules/record_metric Implementation Summary

Purpose: This Lambda function runs every minute to:
- Query GitHub API to count idle vs busy runners for a specific installation
- Publish CloudWatch metrics (IdleRunners and BusyRunners) to the GitHubRunners namespace
- Help autoscaling decisions based on runner availability

Current Resources:
- Lambda function with custom IAM role
- EventBridge rule (runs every 1 minute)
- CloudWatch log group
- Custom packaging via package.sh script
- S3 object for lambda package
- IAM policies for:
    - CloudWatch metrics (put_metric_data)
    - ASG describe operations
    - Secrets Manager access (GitHub credentials)
    - STS GetCallerIdentity

Migration Implementation Plan

Phase 1: Preparation ✅

1. ✅ Add new module dependency
   - ✅ Add terraform-aws-lambda-monitored module source to the module block
   - ✅ Version: Use latest stable version (0.3.1 or newer) - Using v0.3.4
2. ✅ Create custom IAM policy for module
   - ✅ The monitored module creates a base IAM role, but we need to attach additional permissions
   - ✅ Create a separate IAM policy document with the record_metric-specific permissions:
    - ✅ cloudwatch:PutMetricData (with namespace condition)
    - ✅ autoscaling:DescribeAutoScalingGroups
    - ✅ secretsmanager:GetSecretValue (for GitHub credentials)
    - ✅ sts:GetCallerIdentity

Phase 2: Module Integration ✅

3. ✅ Replace lambda resources
   - ✅ Remove: aws_lambda_function.lambda
   - ✅ Remove: aws_iam_role.iam_for_lambda
   - ✅ Remove: All IAM policy attachments
   - ✅ Remove: aws_cloudwatch_log_group.lambda
   - ✅ Remove: Lambda packaging resources (null_resource, data.archive_file, aws_s3_object)
   - ✅ Add: terraform-aws-lambda-monitored module call
4. ✅ Configure the new module
   - ✅ Map current variables to module inputs:
    - ✅ function_name = "${var.asg_name}_record_metric"
    - ✅ lambda_source_dir = "${path.module}/lambda"
    - ✅ architecture = var.architecture
    - ✅ python_version = var.python_version
    - ✅ timeout = var.lambda_timeout
    - ✅ memory_size = 128 (default, can be made configurable)
    - ✅ requirements_file = "${path.module}/lambda/requirements.txt"
    - ✅ cloudwatch_log_group_retention = var.cloudwatch_log_group_retention
      - ✅ Environment variables (same as current):
        - ✅ ASG_NAME, GITHUB_ORG_NAME, GITHUB_SECRET, GITHUB_SECRET_TYPE, GH_APP_ID, INSTALLATION_ID
          - ✅ Alert configuration:
        - ✅ alarm_emails = New variable (optional, can default to empty list if no monitoring needed initially)
    - ✅ error_rate_threshold = 10 (configurable)
5. ✅ Attach custom IAM policy
   - ✅ Create aws_iam_policy resource with the record_metric-specific permissions
   - ✅ Pass the policy ARN to the module via additional_iam_policy_arns parameter

Phase 3: EventBridge Integration ✅

6. ✅ Keep EventBridge rule
   - ✅ The terraform-aws-lambda-monitored module doesn't handle EventBridge scheduling
   - ✅ Keep existing eventbridge.tf file but update references:
    - ✅ Replace aws_lambda_function.lambda.function_name with module.lambda_monitored.lambda_function_name
    - ✅ Replace aws_lambda_function.lambda.arn with module.lambda_monitored.lambda_function_arn

Phase 4: Cleanup ✅

7. ✅ Remove unnecessary files
   - ✅ Delete: lambda.tf (replaced by module)
   - ✅ Delete: lambda_code.tf (packaging handled by module)
   - ✅ Delete: cloudwatch.tf (handled by module)
   - ✅ Delete: package.sh (no longer needed)
   - ✅ Delete: All .zip files in module directory
   - ✅ Keep: main.tf (for custom IAM policy)
   - ✅ Keep: eventbridge.tf (for scheduling)
   - ✅ Keep: variables.tf (updated)
   - ✅ Keep: outputs.tf (updated)
   - ✅ Keep: data-sources.tf (may still be needed)
   - ✅ Delete: locals.tf (no longer needed - DELETED)
   - ✅ Keep: terraform.tf (updated)
   - ✅ Keep: lambda/ directory (source code)
8. ✅ Update outputs
   - ✅ Replace aws_lambda_function.lambda.function_name with module.lambda_monitored.lambda_function_name
   - ✅ May need to add function_arn output from the module
9. ✅ Update terraform.tf
   - ✅ Removed null provider (no longer needed)
   - ✅ Removed random provider (handled by module)
   - ✅ AWS provider version constraints are compatible (>= 5.31, < 7.0)

Phase 5: Variables ✅

10. ✅ Update variables.tf
    - ✅ Add new variable: alarm_emails (list(string), required with validation)
    - ✅ Add new variable: error_rate_threshold (number, optional with default 10.0)
    - ✅ Removed: lambda_bucket_name (module creates its own S3 bucket)
    - ✅ Keep all other existing variables for backward compatibility

Phase 6: Parent Module Integration ✅

11. ✅ Update parent module call (in cloudwatch.tf at root level)
    - ✅ Removed: lambda_bucket_name parameter (variable removed)
    - ✅ Add: alarm_emails parameter
    - ✅ Add: error_rate_threshold parameter
    - ✅ All other parameters remain the same

Phase 7: Testing ⏳

12. ⏳ Test migration
    - ⏳ Run terraform plan to verify changes
    - ⏳ Check for resource replacements vs updates
    - ⏳ Verify EventBridge rule still triggers lambda
    - ⏳ Test lambda execution manually
    - ⏳ Verify CloudWatch metrics are still published
    - ⏳ Verify error alarms work (if configured)

Key Benefits After Migration

- Automated dependency packaging (no more package.sh)
- Built-in error monitoring and alerting via SNS
- Standardized CloudWatch integration
- Reduced boilerplate code (~200 lines removed)
- Consistent IAM role structure
- Better change detection for repackaging
- Future VPC support if needed

Risks & Considerations

1. Lambda function will be recreated (new name/ARN from module)
   - EventBridge rule will need to be updated (handled automatically by terraform)
   - Any external references to lambda ARN will break
2. S3 bucket changes
   - Module creates its own S3 bucket for lambda packages
   - Old lambda packages in var.lambda_bucket_name can be cleaned up later
3. IAM role recreation
   - Lambda will get a new IAM role created by the module
   - Temporary permissions gap during apply (minimal risk)
4. CloudWatch log group
   - May be recreated (logs will be lost unless exported first)
   - Consider exporting logs before migration if history is important

Recommended Approach

Start with a test environment first to validate the migration works correctly, then apply to production.
