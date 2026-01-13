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

The `record_metric` Lambda publishes:

| Metric | Namespace | Description |
|--------|-----------|-------------|
| `IdleRunnersCount` | `InfraHouse/ActionsRunner` | Number of idle runners |

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
| `cpu_utilization` | CPU > threshold | Alert (optional) |

### Lambda Alarms

Each Lambda has:

| Alarm | Condition | Action |
|-------|-----------|--------|
| `*_errors` | Any error (immediate) or error rate > threshold | SNS notification |
| `*_throttles` | Any throttle | SNS notification |

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

### Email Alerts

```hcl
module "actions-runner" {
  alarm_emails = [
    "oncall@example.com",
    "team-leads@example.com"
  ]
}
```

### Custom SNS Topics

For integration with PagerDuty, Slack, or other systems, the module outputs the SNS topic ARN:

```hcl
# After deployment, get the topic ARN
output "alarm_topic_arn" {
  value = module.actions-runner.alarm_topic_arn
}

# Subscribe your custom endpoint
resource "aws_sns_topic_subscription" "pagerduty" {
  topic_arn = module.actions-runner.alarm_topic_arn
  protocol  = "https"
  endpoint  = "https://events.pagerduty.com/integration/xxx/enqueue"
}
```

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
