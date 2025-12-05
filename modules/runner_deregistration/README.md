# Runner Deregistration Module

## Overview

This module manages the **deregistration and cleanup** of GitHub Actions self-hosted runners 
when EC2 instances are terminated. It ensures that runners are properly removed from GitHub 
and that associated resources (like registration tokens in Secrets Manager) are cleaned up.

## What It Does

The module deploys a Lambda function that handles runner deregistration in **two phases**:

### Phase 1: **Lifecycle Hook** (Immediate, during instance termination)
When an EC2 instance is terminating or entering the warm pool:
- Receives ASG lifecycle hook event (`EC2 Instance-terminate Lifecycle Action`)
- **Deletes the registration token** from Secrets Manager (prevents the instance from re-registering)
- **Stops the actions-runner service** on the instance via SSM command
- Completes the lifecycle action to allow ASG to continue termination
- **Does NOT deregister from GitHub** (keeps the lifecycle hook fast and reliable)

**Special case - Warm Pool:** If the instance is in `Warmed:Terminating:Wait` state (terminating
FROM the warm pool), the lambda skips stopping the service and just completes the lifecycle hook.

### Phase 2: **Scheduled Sweep** (Deferred GitHub cleanup)
Runs every **30 minutes** to deregister runners from GitHub:
- Scans all GitHub runners with matching `installation_id` label
- Checks if their EC2 instances are terminated or non-existent
- **Deregisters terminated/orphaned runners from GitHub**
- Handles edge cases (lifecycle hook failures, Lambda timeouts, manual instance terminations)

## Why Two Phases?

This **two-phase architecture** separates time-sensitive operations from potentially slow API calls:

```
Instance Termination Event
  ↓
Lifecycle Hook (seconds)
  ├─ Delete registration token  ✓ Fast, reliable
  ├─ Stop runner service        ✓ Fast, reliable
  └─ Complete lifecycle action  ✓ Instance continues termination

↓ Runner appears "offline" in GitHub (service stopped)

Scheduled Sweep (within 30 min)
  └─ Deregister from GitHub     ✓ Async, retry on failure

↓ Runner removed from GitHub UI
```

**Why not deregister from GitHub immediately?**
1. **Reliability:** GitHub API calls can fail (rate limits, network issues, slow responses)
2. **Speed:** Lifecycle hooks have timeout limits - blocking on GitHub API would delay instance termination
3. **Warm Pool:** Instances entering warm pool need fast lifecycle completion to hibernate quickly
4. **Separation of concerns:** Instance lifecycle (AWS) is decoupled from GitHub state cleanup

**Why 30 minutes?** Orphaned runners showing as "offline" in GitHub UI for up to 30 minutes is
acceptable - they're just stale metadata and won't receive jobs (service is already stopped).
The 30-minute sweep reduces Lambda invocations (83% fewer vs 5 minutes) while providing
reasonable cleanup SLA.

## Requirements

### VPC Configuration
The Lambda **must run in VPC subnets with NAT Gateway** or VPC Endpoints because it needs to:
- Call SSM to stop the actions-runner service
- Call Secrets Manager for GitHub credentials and token cleanup
- Call EC2 APIs to check instance states
- Call AutoScaling APIs to complete lifecycle actions
- Call GitHub APIs to deregister runners

Without NAT or VPC endpoints, the Lambda cannot reach AWS service APIs and will fail.

### IAM Permissions
The Lambda requires extensive AWS permissions:
- **AutoScaling:** `CompleteLifecycleAction`, `Describe*` (ASG, instances, warm pool)
- **EC2:** `DescribeInstances`, `DescribeTags`
- **SSM:** `SendCommand`, `GetCommandInvocation`
- **Secrets Manager:** `GetSecretValue` (GitHub credentials), `DeleteSecret`, `DescribeSecret` (registration tokens)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     EventBridge Triggers                    │
├──────────────────────────┬──────────────────────────────────┤
│  Lifecycle Hook Event    │    Scheduled (every 30 min)      │
│  (Instance Terminating)  │    (Safety net sweep)            │
└────────────┬─────────────┴─────────────┬────────────────────┘
             │                           │
             └───────────┬───────────────┘
                         ↓
            ┌────────────────────────────┐
            │   Lambda Function          │
            │   (runner_deregistration)  │
            │                            │
            │   - VPC-enabled            │
            │   - CloudWatch monitoring  │
            │   - Threshold alerting     │
            └────────┬───────────────────┘
                     │
         ┌───────────┼───────────┬─────────────┐
         ↓           ↓           ↓             ↓
      GitHub      Secrets     EC2/ASG        SSM
      (dereg)     Manager     (check)     (stop service)
```

## Monitoring & Alerting

Uses **threshold-based alerting** (default: 10% error rate):
- Prevents alert fatigue from transient failures (GitHub API rate limits, network issues)
- Alerts only when error rate exceeds 10% → indicates systemic problems
- Lambda runs ~48 times/day (scheduled) + on every instance termination
- Occasional failures are expected and tolerable (best-effort cleanup)

**Alternative:** `alert_strategy = "immediate"` would send an email for every single error, 
but would likely cause alert fatigue.

## Usage

```hcl
module "deregistration" {
  source = "./modules/runner_deregistration"

  asg_name                       = "my-runners"
  cloudwatch_log_group_retention = 365
  github_org_name                = "my-org"
  github_credentials = {
    type   = "token"  # or "pem"
    secret = "arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token"
  }
  github_app_id                    = "123456"
  registration_token_secret_prefix = "github-runner-token"
  lambda_timeout                   = 30
  installation_id                  = "unique-installation-id"

  # VPC Configuration (REQUIRED - subnets MUST have NAT)
  security_group_ids = ["sg-12345678"]
  subnet_ids         = ["subnet-abc123", "subnet-def456"]

  # Monitoring Configuration
  alarm_emails         = ["ops@example.com"]
  error_rate_threshold = 10.0  # Alert when >10% error rate

  # Optional
  python_version = "python3.12"
  architecture   = "x86_64"

  tags = {
    Environment = "production"
  }
}
```

## Variables

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| `asg_name` | Autoscaling group name | `string` | - | yes |
| `alarm_emails` | Email addresses for error notifications | `list(string)` | - | yes |
| `github_org_name` | GitHub organization name | `string` | - | yes |
| `github_credentials` | GitHub auth credentials (token or PEM) | `object({type, secret})` | - | yes |
| `github_app_id` | GitHub App ID | `string` | - | yes |
| `installation_id` | Unique identifier for runners | `string` | - | yes |
| `registration_token_secret_prefix` | Prefix for registration token secrets | `string` | - | yes |
| `security_group_ids` | Security groups for Lambda (VPC) | `list(string)` | - | yes |
| `subnet_ids` | Subnets for Lambda (must have NAT) | `list(string)` | - | yes |
| `cloudwatch_log_group_retention` | CloudWatch log retention days | `number` | 365 | no |
| `error_rate_threshold` | Error rate % for alerting | `number` | 10.0 | no |
| `lambda_timeout` | Lambda timeout in seconds | `number` | 30 | no |
| `python_version` | Python runtime version | `string` | `python3.12` | no |
| `architecture` | Lambda CPU architecture | `string` | `x86_64` | no |

## Outputs

| Name | Description |
|------|-------------|
| `lambda_name` | Name of the deregistration Lambda function |
| `log_group_name` | CloudWatch Log Group name for the deregistration lambda |

## Implementation Details

### Lambda Lifecycle Hook Handler
When processing lifecycle hook events:
1. Detects `LifecycleHookName == "deregistration"`
2. **Deletes the registration token** from Secrets Manager (prevents re-registration)
3. Checks instance lifecycle state for warm pool (`Warmed:Terminating:Wait`)
   - If terminating FROM warm pool → Skip service stop, complete lifecycle hook immediately
4. Sends SSM command to stop actions-runner service
5. Waits for command completion (with timeout)
6. Completes lifecycle action (`CONTINUE` on success, `ABANDON` on failure)
7. **Does NOT call GitHub API** to deregister the runner (deferred to scheduled sweep)

### Lambda Scheduled Sweep Handler
When processing scheduled events:
1. Lists all GitHub runners with `installation_id:<installation_id>` label
2. For each runner, extracts EC2 instance ID from runner name
3. Checks EC2 instance state via `DescribeInstances`
4. If instance is terminated/not found → deregisters runner from GitHub
5. Continues sweep even if individual runners fail (best-effort)

### Error Handling
- Individual runner failures don't stop the sweep
- Lifecycle hook failures result in `ABANDON` (ASG continues termination)
- All errors are logged to CloudWatch
- Threshold alerting prevents notification spam

## Migration Notes

This module uses `terraform-aws-lambda-monitored` (v1.0.4) which provides:
- Automated Lambda code packaging and deployment
- Built-in CloudWatch alarms and SNS notifications
- Configurable error rate thresholds
- Automatic dependency management via Lambda layers

### Breaking Changes from Previous Versions
- **Removed:** `lambda_bucket_name` variable (module creates its own S3 bucket)
- **Added:** `alarm_emails` variable (required for monitoring compliance)
- **Added:** `error_rate_threshold` variable (optional, defaults to 10%)

## Troubleshooting

### Lambda fails with timeout errors
- Check VPC subnets have NAT Gateway or VPC Endpoints
- Increase `lambda_timeout` if SSM commands take longer
- Verify security groups allow outbound traffic

### Runners not being deregistered
- Check CloudWatch Logs for Lambda errors
- Verify lifecycle hook is attached to ASG
- Confirm `installation_id` label matches on runners
- Check GitHub credentials are valid

### Runners still showing in GitHub after instance termination
This is **expected behavior**! The two-phase architecture means:
- Runners appear "offline" immediately (service is stopped)
- Runners are removed from GitHub UI within 30 minutes (scheduled sweep)
- This is by design to keep lifecycle hooks fast and reliable
- If runners aren't removed after 30 minutes, check the scheduled sweep Lambda logs

### Too many alarm emails
- Increase `error_rate_threshold` (e.g., 15% or 20%)
- Consider switching to `alert_strategy = "immediate"` only if zero-tolerance for failures is required

## See Also

- [terraform-aws-lambda-monitored module](https://registry.terraform.io/modules/infrahouse/lambda-monitored/aws)
- Parent module: `terraform-aws-actions-runner`
- Related: `runner_registration` module
