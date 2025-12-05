# Migration Plan: runner_deregistration to terraform-aws-lambda-monitored

## Overview
Migrate `modules/runner_deregistration` to use the `infrahouse/lambda-monitored/aws` module (version 1.0.4), 
following the same pattern used for `modules/record_metric`.

## Current State Analysis

### Files to be Modified/Replaced
- `lambda.tf` - Contains custom Lambda setup with manual IAM management (~204 lines)
- `lambda_code.tf` - Custom packaging script with S3 upload (~74 lines)
- `cloudwatch.tf` - Manual CloudWatch Log Group and EventBridge rules (~41 lines)
- `eventbridge.tf` - Additional EventBridge scheduled trigger (~35 lines)
- `variables.tf` - Variable definitions
- `outputs.tf` - Lambda function name output
- `main.tf` - Currently empty

### Current Features
1. **Lambda Function:**
   - Function name: `${var.asg_name}_deregistration`
   - Handler: `main.lambda_handler`
   - Runtime: `var.python_version` (default: python3.12)
   - Timeout: `var.lambda_timeout` (default: 30s)
   - Architecture: `var.architecture`
   - VPC Config: `security_group_ids` and `subnet_ids`

2. **IAM Permissions:**
   - `sts:GetCallerIdentity`
   - `autoscaling:CompleteLifecycleAction`
   - `autoscaling:Describe*` (AutoScalingGroups, Instances, WarmPool)
   - `ec2:DescribeInstances`, `ec2:DescribeTags`
   - `ssm:SendCommand`, `ssm:GetCommandInvocation`
   - `secretsmanager:GetSecretValue` (for GitHub credentials)
   - `secretsmanager:DeleteSecret`, `secretsmanager:DescribeSecret` (for registration tokens)

3. **EventBridge Triggers:**
   - **Scheduled:** Every 30 minutes (rate schedule) - safety net for cleanup
   - **Lifecycle Hook:** ASG EC2 Instance-terminate Lifecycle Action - primary cleanup mechanism

4. **Environment Variables:**
   - `ASG_NAME`
   - `REGISTRATION_TOKEN_SECRET_PREFIX`
   - `GITHUB_ORG_NAME`
   - `GITHUB_SECRET`
   - `GITHUB_SECRET_TYPE`
   - `GH_APP_ID`
   - `INSTALLATION_ID`

5. **Custom Packaging:**
   - Uses `package.sh` script
   - Manual S3 upload with custom wait logic
   - Dependency tracking via file hashes

## Target State (After Migration)

### New Structure
- `main.tf` - Will contain the lambda-monitored module call and custom IAM policy
- `eventbridge.tf` - Keep existing EventBridge triggers (both scheduled and lifecycle)
- Delete: `lambda.tf`, `lambda_code.tf`, `cloudwatch.tf`, `locals.tf`

### Module Configuration
```hcl
module "lambda_monitored" {
  source  = "registry.infrahouse.com/infrahouse/lambda-monitored/aws"
  version = "1.0.4"

  function_name                 = "${var.asg_name}_deregistration"
  lambda_source_dir             = "${path.module}/lambda"
  architecture                  = var.architecture
  python_version                = var.python_version
  timeout                       = var.lambda_timeout
  memory_size                   = 128
  cloudwatch_log_retention_days = var.cloudwatch_log_group_retention
  alarm_emails                  = var.alarm_emails
  alert_strategy                = "threshold"
  error_rate_threshold          = var.error_rate_threshold
  additional_iam_policy_arns    = [aws_iam_policy.runner_deregistration_permissions.arn]

  # VPC Configuration
  subnet_ids         = var.subnet_ids
  security_group_ids = var.security_group_ids

  environment_variables = {
    ASG_NAME                           = var.asg_name
    REGISTRATION_TOKEN_SECRET_PREFIX   = var.registration_token_secret_prefix
    GITHUB_ORG_NAME                    = var.github_org_name
    GITHUB_SECRET                      = var.github_credentials.secret
    GITHUB_SECRET_TYPE                 = var.github_credentials.type
    GH_APP_ID                          = var.github_app_id
    INSTALLATION_ID                    = var.installation_id
  }

  tags = merge(
    var.tags,
    {
      "asg_name" : var.asg_name
    }
  )
}
```

## Migration Steps

### 1. Update variables.tf
- [x] **Add** new required variables:
  ```hcl
  variable "alarm_emails" {
    description = "List of email addresses to receive alarm notifications for Lambda errors. At least one email is required for ISO 27001 compliance."
    type        = list(string)
    validation {
      condition     = length(var.alarm_emails) > 0
      error_message = "At least one alarm email address must be provided for monitoring compliance"
    }
  }

  variable "error_rate_threshold" {
    description = "Error rate threshold percentage for threshold-based alerting."
    type        = number
    default     = 10.0
    validation {
      condition     = var.error_rate_threshold > 0 && var.error_rate_threshold <= 100
      error_message = "error_rate_threshold must be between 0 and 100"
    }
  }
  ```

- [x] **Remove** variables (breaking changes):
  - `lambda_bucket_name` - module creates its own S3 bucket
  - Note: `security_group_ids` and `subnet_ids` are KEPT (still needed for module interface)

### 2. Create main.tf with lambda-monitored module
- [x] Create custom IAM policy document for runner_deregistration permissions
- [x] Create IAM policy resource
- [x] Add lambda-monitored module call with all configurations
- [x] Ensure VPC config is passed to module (subnet_ids, security_group_ids)

### 3. Update eventbridge.tf
- [x] Update Lambda function references from `aws_lambda_function.lambda` to `module.lambda_monitored.lambda_function`
- [x] Update Lambda ARN references to `module.lambda_monitored.lambda_arn`
- [x] Keep both EventBridge rules (scheduled and lifecycle hook)
- [x] Update Lambda permission resources to reference the new module
- [x] Change scheduled interval from 5 to 30 minutes
- [x] Move lifecycle hook EventBridge rule from cloudwatch.tf to eventbridge.tf

### 4. Update outputs.tf
- [x] Change output from `aws_lambda_function.lambda.function_name` to `module.lambda_monitored.lambda_name`

### 5. Delete obsolete files
- [x] Delete `lambda.tf` - replaced by lambda-monitored module
- [x] Delete `lambda_code.tf` - packaging handled by module
- [x] Delete `cloudwatch.tf` - CloudWatch resources handled by module (except EventBridge rules moved to eventbridge.tf)
- [x] Delete `locals.tf` - no longer needed
- [x] Delete `package.sh` - packaging handled by module

### 6. Update parent module registration.tf
- [x] Update module "deregistration" call in registration.tf:23-44
- [x] Add `alarm_emails = var.alarm_emails` parameter
- [x] Add `error_rate_threshold = var.error_rate_threshold` parameter
- [x] Remove `lambda_bucket_name` parameter
- [x] Note: Both variables already exist in parent variables.tf (alarm_emails:49, error_rate_threshold:63)
- [x] Note: `security_group_ids` and `subnet_ids` remain in module interface

### 7. Update terraform.tf version constraints
- [x] Verify module version constraint is set to `1.0.4` in main.tf
- [x] Remove unused `null` and `random` providers from terraform.tf
- [x] Match record_metric module's provider configuration (aws only)

## Breaking Changes

### Removed Variables
1. **`lambda_bucket_name`**
   - **Reason:** The `terraform-aws-lambda-monitored` module creates and manages its own S3 bucket for Lambda code
   - **Migration:** Simply remove this variable from module calls
   - **Impact:** Users upgrading will need to update their module invocations

### Preserved Variables (Not Breaking)
- `security_group_ids` - Still required, passed to lambda-monitored module
- `subnet_ids` - Still required, passed to lambda-monitored module

### New Required Variables
1. **`alarm_emails`** (required)
   - List of email addresses for error monitoring
   - Required for compliance
   - At least one email must be provided

2. **`error_rate_threshold`** (optional)
   - Default: 10.0
   - Controls alerting sensitivity

## Benefits of Migration

1. **Automated Dependency Management**
   - No more custom `package.sh` script
   - Built-in pip dependency installation
   - Automatic Lambda layer creation for dependencies

2. **Built-in Error Monitoring**
   - Automated CloudWatch alarms
   - SNS topic for notifications
   - Configurable error rate thresholds

3. **Standardized Configuration**
   - Consistent with other lambda functions in the project
   - Follows module best practices
   - Easier to maintain

4. **Reduced Boilerplate**
   - ~204 lines in lambda.tf → replaced by module
   - ~74 lines in lambda_code.tf → handled by module
   - Partial cloudwatch.tf → handled by module
   - Total reduction: ~300+ lines of custom code

5. **Improved Reliability**
   - Module is tested and maintained
   - Handles edge cases
   - Better error handling

## Special Considerations

### 1. Multiple EventBridge Triggers
Unlike `record_metric` which had only one trigger, `runner_deregistration` has TWO:
- Scheduled trigger (every 30 minutes) - safety net for cleanup failures
- Lifecycle hook trigger (on instance termination) - primary cleanup mechanism

Both triggers must be preserved and updated to reference the new module.

### 2. VPC Configuration
The lambda-monitored module supports VPC configuration, which is required for runner_deregistration:
- Pass `subnet_ids` to module
- Pass `security_group_ids` to module

### 3. Complex IAM Permissions
Runner deregistration requires extensive AWS permissions:
- EC2 and SSM access for runner management
- ASG lifecycle actions
- Secrets Manager for token cleanup

All permissions must be consolidated into a single IAM policy document in main.tf.

### 4. Lambda Invocation Config
Current lambda.tf includes:
```hcl
resource "aws_lambda_function_event_invoke_config" "update_filter" {
  function_name          = aws_lambda_function.lambda.function_name
  maximum_retry_attempts = 0
}
```

Check if lambda-monitored module handles this or if it needs to be added separately.

## Design Decisions

### 1. Alert Strategy: "threshold" vs "immediate"

**Decision:** Use `alert_strategy = "threshold"` with `error_rate_threshold = 10.0` (default)

**Rationale:**
- **High Invocation Frequency:** The Lambda runs every 30 minutes (48 invocations/day) plus on every instance termination
- **Transient Failures Expected:** GitHub API rate limits, network issues, temporary AWS service issues
- **Alert Fatigue Prevention:** "immediate" strategy would send an email for every single error, leading to alert fatigue
- **Meaningful Alerts:** "threshold" strategy (>10% error rate) indicates systemic problems worth investigating
- **Best-Effort Operations:** The function performs cleanup tasks; occasional failures won't break the system
- **Consistency:** Same strategy used in `record_metric` module for similar reasons

**Alternative Considered:** `alert_strategy = "immediate"`
- Would notify on every single error
- Appropriate only if zero-tolerance for any failures is required
- Risk: Operators may start ignoring frequent alerts

### 2. Scheduled Sweep Interval: 30 minutes

**Decision:** Changed from 5 minutes to 30 minutes

**Rationale:**
- **Primary Cleanup Mechanism:** The ASG lifecycle hook (configured in parent `main.tf:239-245`) handles immediate cleanup when instances terminate
- **Safety Net Role:** The scheduled sweep is only for edge cases where lifecycle hooks fail:
  - Hook timeouts or Lambda failures
  - Warm pool edge cases
  - Manual instance terminations
  - Orphaned runners (GitHub shows offline, EC2 already terminated)
- **Cost/Benefit Analysis:**
  - 5 minutes: 288 invocations/day, ~2.5 min average cleanup time
  - 30 minutes: 48 invocations/day (83% reduction), ~15 min average cleanup time
  - Orphaned runners are NOT urgent (they're just stale metadata, not consuming resources)
- **API Rate Limit Risk:** Fewer invocations = fewer GitHub API calls = lower rate limit risk
- **Acceptable SLA:** 15-minute average cleanup delay for orphaned runners is perfectly reasonable

**Previous Value:** 5 minutes (every 5 minutes)
- Overly aggressive for a safety net mechanism
- Higher Lambda costs and API usage
- Only beneficial if pristine GitHub UI at all times is critical

**Dual Trigger Architecture:**
```
Lifecycle Hook (immediate)     → Primary cleanup (seconds)
  ↓ (if fails)
Scheduled Sweep (30 min)       → Safety net (catches failures within 30 min)
```

## Testing Strategy

### During Development

**Iterative Testing with `make test-keep`:**
- [x] Run `make test-keep` - deploys infrastructure and keeps it running
- [x] Manual verification while infrastructure is live:
  - [x] Check Lambda function in AWS Console
  - [x] Verify environment variables are set correctly
  - [x] Check both EventBridge rules are attached (scheduled + lifecycle hook)
  - [x] Verify IAM role has correct permissions
  - [x] Check CloudWatch Logs for invocations
  - [x] Verify SNS topic created for alarms
  - [x] Test scheduled execution (wait for 30-minute trigger)
  - [x] Test lifecycle hook (trigger instance termination)
  - [x] Verify Lambda can access VPC resources
- [x] Code formatting and validation:
  - [x] `terraform fmt -recursive`
  - [x] `terraform-docs .`
- [x] Iterate on fixes as needed with infrastructure still deployed

### After PR Creation

**CI System Full Test Suite:**
- CI will automatically run the complete test suite
- Tests will create infrastructure, run validations, and destroy everything
- This ensures no regressions and validates the migration end-to-end
- CI tests include:
  - Full deployment and teardown cycle
  - All Lambda invocations and triggers
  - Error scenarios and alerting validation
  - No manual intervention required

## Documentation Updates

- [ ] Update README.md with:
  - New required variable: `alarm_emails`
  - New optional variable: `error_rate_threshold`
  - Breaking change: removed `lambda_bucket_name`
  - Migration guide
- [ ] Update examples if any exist
- [ ] Update CHANGELOG or create migration notes

## Success Criteria

- [ ] All Terraform code validates and formats correctly
- [ ] Tests pass successfully
- [ ] Lambda function deploys and runs correctly
- [ ] Both EventBridge triggers work as expected
- [ ] VPC connectivity maintained
- [ ] Error monitoring and alerting operational
- [ ] Documentation updated
- [ ] No regression in functionality

## Timeline Estimate

- **Code Changes:** 2-3 hours
- **Testing:** 1-2 hours
- **Documentation:** 30 minutes
- **Total:** 4-6 hours

## Notes

- Follow the same pattern used in `modules/record_metric` migration
- Reference `.claude/plan-record_metric-monitored.md` for guidance
- Preserve all existing functionality while gaining monitoring benefits
- This is module 2 of 3 Lambda modules to migrate (record_metric ✅, runner_deregistration ⏳, runner_registration ⏭️)
