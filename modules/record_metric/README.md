# Record Metric Module

## Overview

This module manages **real-time CloudWatch metrics** for GitHub Actions self-hosted runners. 
It continuously monitors runner status and publishes custom metrics to CloudWatch, 
enabling autoscaling decisions and operational visibility.

## What It Does

The module deploys a Lambda function that runs **every minute** to:
1. Query GitHub API for all runners with matching `installation_id` label
2. Check each online runner's status (busy or idle)
3. Count busy vs idle runners
4. Publish metrics to CloudWatch namespace `GitHubRunners`

### Published Metrics

Two custom CloudWatch metrics are published every minute:

| Metric Name | Description | Unit | Dimension |
|-------------|-------------|------|-----------|
| `BusyRunners` | Number of runners currently executing jobs | Count | `asg_name` |
| `IdleRunners` | Number of runners online but not executing jobs | Count | `asg_name` |

These metrics are used by:
- **Autoscaling policies** to scale the ASG based on idle runner count
- **CloudWatch dashboards** for operational monitoring
- **CloudWatch alarms** for capacity alerts

## How It Works

```
Every 1 Minute (EventBridge Schedule)
  ↓
Lambda Invocation
  ↓
GitHub API Query
  ├─ Get all runners with installation_id label
  ├─ Filter to only "online" runners
  └─ Count: busy vs idle
  ↓
CloudWatch PutMetricData
  ├─ MetricName: BusyRunners, Value: X
  └─ MetricName: IdleRunners, Value: Y
  ↓
Autoscaling Policy Uses Metrics
  └─ Target: Keep N idle runners available
```

## Why Every Minute?

The 1-minute frequency provides:
- **Fast autoscaling response** - ASG can react quickly to demand changes
- **Accurate capacity planning** - Real-time view of runner utilization
- **Minimal lag** - Workflow jobs get runners within ~1-2 minutes

**Trade-offs:**
- ~1,440 Lambda invocations/day per ASG
- Minimal cost (Lambda runs for ~1-2 seconds)
- GitHub API calls count against rate limits (typically not an issue)

## Requirements

### GitHub API Access
The Lambda needs to:
- Authenticate with GitHub (via PAT or GitHub App)
- Call GitHub Actions API to list runners
- Access restricted to organization-level runner queries

### AWS Permissions
The Lambda requires:
- **Secrets Manager:** `GetSecretValue` (GitHub credentials)
- **CloudWatch:** `PutMetricData` (restricted to `GitHubRunners` namespace)
- **AutoScaling:** `DescribeAutoScalingGroups` (ASG information)

### No VPC Required
Unlike `runner_registration` and `runner_deregistration`, this Lambda **does not need VPC configuration** because:
- No SSM commands to EC2 instances
- Only GitHub API (internet) and AWS APIs (via AWS SDK)
- Can run without VPC attachment (simpler, faster cold starts)

## Architecture

```
┌─────────────────────────────────┐
│   EventBridge Schedule          │
│   (rate: 1 minute)              │
└────────────┬────────────────────┘
             ↓
┌────────────────────────────────┐
│   Lambda Function              │
│   (record_metric)              │
│                                │
│   - No VPC required            │
│   - CloudWatch monitoring      │
│   - Threshold alerting         │
└────────┬───────────┬───────────┘
         │           │
         ↓           ↓
    GitHub API   CloudWatch
    (list         (publish
     runners)      metrics)
```

## Monitoring & Alerting

Uses **threshold-based alerting** (default: 10% error rate):
- Lambda runs ~1,440 times/day
- Transient failures are expected (GitHub API rate limits, temporary network issues)
- Alerts only when error rate exceeds 10% → indicates systemic problems
- Prevents alert fatigue from occasional GitHub API hiccups

**Alternative:** `alert_strategy = "immediate"` would send an email for every single error, but would cause excessive alerts.

## Usage

```hcl
module "record_metric" {
  source = "./modules/record_metric"

  asg_name                       = "my-runners"
  cloudwatch_log_group_retention = 365
  github_org_name                = "my-org"
  github_credentials = {
    type   = "token"  # or "pem" for GitHub App
    secret = "arn:aws:secretsmanager:us-west-2:123456789012:secret:github-token"
  }
  github_app_id   = "123456"  # Required if using GitHub App
  installation_id = "unique-installation-id"
  lambda_timeout  = 30

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
| `github_app_id` | GitHub App ID (required if using GitHub App) | `string` | - | yes |
| `installation_id` | Unique identifier for runners | `string` | - | yes |
| `cloudwatch_log_group_retention` | CloudWatch log retention days | `number` | 365 | no |
| `error_rate_threshold` | Error rate % for alerting | `number` | 10.0 | no |
| `lambda_timeout` | Lambda timeout in seconds | `number` | 30 | no |
| `python_version` | Python runtime version | `string` | `python3.12` | no |
| `architecture` | Lambda CPU architecture | `string` | `x86_64` | no |

## Outputs

| Name | Description |
|------|-------------|
| `lambda_name` | Name of the record_metric Lambda function |

## Implementation Details

### Lambda Handler Logic
```python
def lambda_handler(event, context):
    # 1. Authenticate with GitHub
    github = GitHubAuth(get_github_token(), org_name)
    gha = GitHubActions(github)

    # 2. Find all runners with installation_id label
    runners = gha.find_runners_by_label(f"installation_id:{installation_id}")

    # 3. Count busy vs idle (only online runners)
    status_counts = Counter()
    for runner in runners:
        if runner.status == "online":
            status_counts["busy" if runner.busy else "idle"] += 1

    # 4. Publish to CloudWatch
    cloudwatch.put_metric_data(
        Namespace="GitHubRunners",
        MetricData=[
            {"MetricName": "BusyRunners", "Value": status_counts["busy"]},
            {"MetricName": "IdleRunners", "Value": status_counts["idle"]},
        ]
    )
```

### Runner Status Logic
- **Counted as busy:** `runner.status == "online" AND runner.busy == True`
- **Counted as idle:** `runner.status == "online" AND runner.busy == False`
- **Not counted:** `runner.status == "offline"` (instance terminated or starting up)

### GitHub Authentication
Supports two authentication methods:
1. **Personal Access Token (PAT):**
   - Set `github_credentials.type = "token"`
   - Token stored in Secrets Manager

2. **GitHub App:**
   - Set `github_credentials.type = "pem"`
   - PEM key stored in Secrets Manager
   - App ID specified in `github_app_id`
   - Generates temporary installation tokens (expires in 1 hour)

## CloudWatch Metrics Usage

### Autoscaling Example
```hcl
resource "aws_autoscaling_policy" "scale_based_on_idle_runners" {
  name                   = "scale-on-idle-runners"
  autoscaling_group_name = aws_autoscaling_group.runners.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    customized_metric_specification {
      metric_dimension {
        name  = "asg_name"
        value = var.asg_name
      }
      metric_name = "IdleRunners"
      namespace   = "GitHubRunners"
      statistic   = "Average"
    }
    target_value = 2.0  # Keep 2 idle runners available
  }
}
```

### CloudWatch Dashboard Example
```hcl
resource "aws_cloudwatch_dashboard" "runners" {
  dashboard_name = "github-runners"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          metrics = [
            ["GitHubRunners", "BusyRunners", { stat = "Average" }],
            [".", "IdleRunners", { stat = "Average" }],
          ]
          period = 60
          stat   = "Average"
          region = "us-west-2"
          title  = "Runner Utilization"
        }
      }
    ]
  })
}
```

## Troubleshooting

### Metrics not appearing in CloudWatch
- Check CloudWatch Logs for Lambda errors
- Verify `installation_id` label exists on runners
- Confirm GitHub credentials are valid
- Check IAM permissions for `cloudwatch:PutMetricData`

### Lambda timeout errors
- Increase `lambda_timeout` if you have many runners (>100)
- Check GitHub API rate limits
- Verify network connectivity to GitHub API

### Too many alarm emails
- Increase `error_rate_threshold` (e.g., 15% or 20%)
- GitHub API rate limits can cause transient failures
- Consider switching to `alert_strategy = "immediate"` only if zero-tolerance required

### Metrics show 0 when runners exist
- Check that runners are **online** (status must be "online", not "offline")
- Verify runners have the correct `installation_id:<value>` label
- Check Lambda logs for GitHub API errors

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

## Performance Considerations

### Lambda Execution Time
- Typical execution: 1-2 seconds
- Scales with number of runners
- ~100 runners: <3 seconds
- ~500 runners: <10 seconds

### GitHub API Rate Limits
- Organization-level runner queries: 1 API call per execution
- ~1,440 calls/day (one per minute)
- GitHub rate limit: 5,000 requests/hour (Enterprise Cloud)
- **Impact:** Minimal, uses <3% of available rate limit

### Cost Estimate
- Lambda invocations: ~43,200/month (1,440/day × 30 days)
- Execution time: ~2 seconds average
- AWS Free Tier: 1M requests + 400,000 GB-seconds free/month
- **Monthly cost:** Typically $0 (within free tier)

## See Also

- [terraform-aws-lambda-monitored module](https://registry.terraform.io/modules/infrahouse/lambda-monitored/aws)
- Parent module: `terraform-aws-actions-runner`
- Related: `runner_registration`, `runner_deregistration` modules
- [GitHub Actions REST API - Self-hosted runners](https://docs.github.com/en/rest/actions/self-hosted-runners)
