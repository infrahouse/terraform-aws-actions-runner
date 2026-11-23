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

  # Error rate (%) above which the deregistration and record_metric error alarms fire (default 10).
  # The registration error alarm fires on any error and ignores it.
  error_rate_threshold = 10.0
}
```

How each function's error alarm works is described under [Lambda Alarms](#lambda-alarms).

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

### EC2 and ASG Alarms

Created automatically, all routed to the module-owned SNS topic (and to any ARNs in `alarm_topic_arns`):

| Alarm | Condition | Why |
|-------|-----------|-----|
| `CPU Alarm on ASG <name>` | Average CPU > 90% for 1 minute | Runner under sustained load |
| `ASGAtMax-<name>` | `GroupInServiceInstances >= asg_max_size` for 5 minutes | Saturation — ceiling may need raising |
| `ASGZeroInService-<name>` | `GroupInServiceInstances == 0` while `GroupDesiredCapacity > 0` for 10 minutes | Catastrophic — ASG wants instances but has none |
| `WarmPoolEmpty-<name>` | `WarmPoolWarmedCapacity == 0` while `WarmPoolDesiredCapacity > 0` for 10 minutes (when warm pool enabled) | Latency-hiding optimization is off |
| `ASGLaunchStuck-<name>` | `GroupPendingInstances > 0` sustained >20 minutes | Likely launch failure (LT, capacity, IAM). 20-min threshold sits above Puppet's ~15-min provisioning window. |
| `RunnerRegistrationGap-<name>` | `GroupInServiceInstances - (BusyRunners + IdleRunners) > 0` for >5 minutes | EC2 is InService but runner never registered with GitHub |
| `ASGSaturatedAtMax-<name>` | At max size with every runner busy for 10 minutes | Scale-out cannot help; jobs are queueing |

### Lambda Alarms

Each Lambda function has an error alarm, a throttle alarm and a memory alarm, created by
terraform-aws-lambda-monitored. Their names start with the function name: `<asg-name>_registration`,
`<asg-name>_deregistration` or `<asg-name>_record_metric`.

| Alarm | Condition | Action |
|-------|-----------|--------|
| `<function>-errors-immediate` | Any error (registration) | SNS notification |
| `<function>-errors-threshold` | Error rate > `error_rate_threshold` (deregistration, record_metric) | SNS notification |
| `<function>-throttles` | Any throttle | SNS notification |
| `<function>-memory` | Memory utilization > 80% | SNS notification |

#### Error alarms

An error-rate alarm only works if every one of its periods contains an invocation. CloudWatch has no datapoint for a
period in which the function didn't run, and the alarms treat missing data as not breaching. With periods shorter
than the gap between invocations, the alarm never collects enough breaching datapoints and stays OK while every run
fails ([#91](https://github.com/infrahouse/terraform-aws-actions-runner/issues/91)). So each function's error alarm
follows how often the function runs:

| Function | Runs | Error alarm | Fires after |
|----------|------|-------------|-------------|
| registration | On ASG launch lifecycle events | `-errors-immediate`: any error | The first failed registration |
| deregistration | Every 30 minutes, and on ASG terminate lifecycle events | `-errors-threshold`: 30-minute periods, 2 of 2 | Two consecutive half-hours with an error rate above `error_rate_threshold`: about an hour of failing sweeps |
| record_metric | Every minute | `-errors-threshold`: 1-minute periods, 2 of 2 | Two consecutive minutes with an error rate above `error_rate_threshold` |

- **registration is event-driven.** Launches can be hours or days apart, so no error-rate period spans the gap
  between invocations, and a failed registration means an instance that doesn't join the pool. The alarm fires on
  any error. Lambda stamps an error with the minute the invocation started but publishes it when the invocation
  ends, so lambda-monitored sizes this alarm's window from the function's timeout: an error late in a 15-minute
  invocation still alarms.
- **deregistration runs on a sparse schedule.** A sweep every 30 minutes removes runners whose instances are gone.
  The alarm uses one 30-minute period per sweep, derived from the schedule, so every period has an invocation. One
  failed sweep doesn't alarm, because the next sweep retries the cleanup; two in a row do. The period is also longer
  than the function's timeout (`allowed_drain_time`, at most 900 seconds), so a slow run's datapoint lands inside the
  window. With one sweep per period, a period's error rate is 0% or 100% unless terminations add invocations to it,
  so `error_rate_threshold` changes little for this alarm.
- **record_metric runs every minute.** Every 1-minute period has an invocation, so the default window works, and
  two failing minutes in a row alarm. A short window suits it: the scaling alarms run on the metrics it publishes.

!!! note "Registration alarm renamed"
    Releases after 6.0.0 replace `<asg-name>_registration-errors-threshold` with
    `<asg-name>_registration-errors-immediate`. Update anything that refers to the old alarm by name. See
    [Upgrading](upgrading.md#upgrading-past-600-lambda-error-alarms).

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

## CloudWatch Dashboard

The module creates a CloudWatch dashboard named after the ASG. It surfaces, in order:

1. Alarm state for every alarm this module owns.
2. `BusyRunners` / `IdleRunners` and derived utilization.
3. Fleet size (desired / in-service / min / max) and transient states (pending, terminating, standby).
4. Warm pool capacity (when warm pool is enabled).
5. `IdleRunners` with scale-out/scale-in thresholds annotated, plus autoscaling alarm state.
6. EC2 CPU (average + p95) and status-check failures.
7. Lambda lifecycle — invocations / errors / throttles / p95 duration for registration, deregistration, and record_metric.

```hcl
# URL available as an output
output "runner_dashboard" {
  value = module.actions-runner.dashboard_url
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

| Output | Description |
|--------|-------------|
| `autoscaling_group_name` | ASG name for CloudWatch queries |
| `alarm_topic_arn` | Module-owned SNS topic ARN; subscribe additional endpoints here |
| `dashboard_name` | Name of the CloudWatch dashboard |
| `dashboard_url` | Deep link to the CloudWatch dashboard |
| `deregistration_log_group` | CloudWatch log group for deregistration Lambda |
