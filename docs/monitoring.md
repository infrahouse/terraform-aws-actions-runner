# Monitoring

This module includes built-in monitoring designed to meet ISO 27001 compliance requirements.

## Lambda Monitoring

All Lambda functions use the [`terraform-aws-lambda-monitored`](https://registry.infrahouse.com/infrahouse/lambda-monitored/aws) module, which provides:

- **Error alerting** via SNS
- **Throttle monitoring**
- **CloudWatch log retention** (configurable, default 365 days)
- **Configurable alert strategies**

### Required Configuration

```hcl
module "actions-runner" {
  # ... other config ...

  # Required: at least one email for alerts
  alarm_emails = ["oncall@example.com", "team@example.com"]
}
```

!!! warning "Email Confirmation Required"
    AWS sends confirmation emails to each address. Recipients **must click the confirmation link** to receive alerts.

### Alert Configuration

```hcl
module "actions-runner" {
  # ... other config ...

  alarm_emails = ["oncall@example.com"]
  
  # Alert when error rate exceeds 10% (default)
  error_rate_threshold = 10.0
}
```

## CloudWatch Metrics

### Custom Metrics

The `record_metric` Lambda publishes, under the `GitHubRunners` namespace
with an `asg_name` dimension:

| Metric | Description |
|--------|-------------|
| `BusyRunners` | Number of runners currently executing a job |
| `IdleRunners` | Number of registered runners waiting for work |

### AWS Metrics

Standard CloudWatch metrics for:

- ASG: `GroupInServiceInstances`, `GroupDesiredCapacity`
- Lambda: `Invocations`, `Errors`, `Duration`, `Throttles`
- EC2: `CPUUtilization`, `StatusCheckFailed`

## CloudWatch Alarms

### Autoscaling Alarms

Created automatically:

| Alarm | Condition | Action |
|-------|-----------|--------|
| `idle_runners_low` | Idle < target | Scale out |
| `idle_runners_high` | Idle > target | Scale in |
| `cpu_utilization` | CPU > 90% | Alert |

### Lambda Alarms

Each Lambda has:

| Alarm | Condition | Action |
|-------|-----------|--------|
| `*_errors` | Any error (immediate) or error rate > threshold | SNS notification |
| `*_throttles` | Any throttle | SNS notification |
| `*_memory` | Memory utilization > 80% | SNS notification |

The memory alarm is backed by the `LambdaInsights/memory_utilization` metric.
Lambda Insights is enabled on every function in this module so the alarm can
fire before a function runs out of memory — the originally reported incident
was a silent OOM in the `runner_deregistration` sweep that left stale runners
in GitHub. If the alarm ever fires, check the function's recent invocations in
CloudWatch Logs Insights and either reduce memory pressure in the handler or
increase the function's `memory_size`.

## Log Retention

All logs are retained in CloudWatch with configurable retention:

```hcl
module "actions-runner" {
  # ... other config ...

  # Retain logs for 1 year (default: 365)
  cloudwatch_log_group_retention = 365
}
```

### Log Groups Created

- `/aws/lambda/{asg-name}_registration`
- `/aws/lambda/{asg-name}_deregistration`
- `/aws/lambda/{asg-name}_record_metric`

## Compliance Considerations

### ISO 27001

This module addresses several ISO 27001 controls:

| Control | How It's Addressed |
|---------|-------------------|
| A.12.4.1 Event logging | CloudWatch Logs with retention |
| A.12.4.3 Administrator logs | Lambda execution logs |
| A.16.1.2 Reporting security events | SNS alerting on errors |

### SOC 2

Relevant for:

- **CC7.2**: Monitoring system components
- **CC7.3**: Evaluating security events

### Vanta Integration

The module's monitoring setup satisfies Vanta's AWS Lambda checks:

- ✅ CloudWatch alarms on Lambda errors
- ✅ Log retention policies configured
- ✅ Encryption at rest (via AWS-managed keys)

## SNS Integration

The module always creates its own SNS topic for alarm notifications and
subscribes every address in `alarm_emails` to it. This is the required,
load-bearing notification path — every operator gets at least one working
alert channel.

### Email Alerts

```hcl
module "actions-runner" {
  alarm_emails = [
    "oncall@example.com",
    "team-leads@example.com"
  ]
}
```

!!! warning "Email Confirmation Required"
    AWS sends each subscriber a confirmation email. Recipients **must click
    the confirmation link** before they will receive alerts.

### Fan Out to PagerDuty / Slack / Shared Topics

For additional routing, pass one or more existing SNS topic ARNs via
`alarm_topic_arns`. Every alarm this module creates will send to the
module-owned topic **and** to each ARN in the list:

```hcl
module "actions-runner" {
  alarm_emails     = ["oncall@example.com"]
  alarm_topic_arns = [
    aws_sns_topic.pagerduty_bridge.arn,
    aws_sns_topic.shared_org_alerts.arn,
  ]
}
```

### Subscribing Additional Endpoints to the Module-Owned Topic

The module's topic ARN is exposed as an output:

```hcl
resource "aws_sns_topic_subscription" "slack" {
  topic_arn = module.actions-runner.alarm_topic_arn
  protocol  = "https"
  endpoint  = "https://hooks.slack.com/services/..."
}
```

### Host-Level Alarms (Disk, Memory)

Not shipped yet. The runner AMI does not run the CloudWatch agent today, so
disk and memory metrics are not available. Tracked in
[infrahouse/puppet-code#270](https://github.com/infrahouse/puppet-code/issues/270);
once the agent is wired into `role::github_runner`, a follow-up release of
this module will add disk and memory alarms unconditionally.

## Debugging

### Check Lambda Logs

```bash
# View registration Lambda logs
aws logs tail /aws/lambda/actions-runner-xyz_registration --follow

# View deregistration Lambda logs
aws logs tail /aws/lambda/actions-runner-xyz_deregistration --follow

# View metric Lambda logs
aws logs tail /aws/lambda/actions-runner-xyz_record_metric --follow
```

### Check Runner Status

```bash
# List runners via GitHub CLI
gh api orgs/{org}/actions/runners --jq '.runners[] | {name, status, busy}'
```

### Check ASG Status

```bash
# Get ASG instances
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$(terraform output -raw autoscaling_group_name)" \
  --query 'AutoScalingGroups[0].Instances'

# Get warm pool instances
aws autoscaling describe-warm-pool \
  --auto-scaling-group-name "$(terraform output -raw autoscaling_group_name)"
```

## Outputs

The module provides these monitoring-related outputs:

```hcl
output "autoscaling_group_name" {
  description = "ASG name for CloudWatch queries"
}

output "deregistration_log_group" {
  description = "CloudWatch log group for deregistration Lambda"
}
```
