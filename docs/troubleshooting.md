# Troubleshooting

Common issues and their solutions.

## Runner Not Registering

### Symptoms
- Instance launches but doesn't appear in GitHub runners list
- ASG shows instances in `Pending` state for a long time

### Check 1: Registration Lambda Logs

```bash
aws logs tail \
  /aws/lambda/$(terraform output -raw autoscaling_group_name)_registration \
  --follow
```

Look for:
- Authentication errors (invalid token/App credentials)
- Rate limiting from GitHub API
- Network errors reaching GitHub

### Check 2: Instance Cloud-init Logs

SSH to the instance or use SSM:

```bash
# Cloud-init output
cat /var/log/cloud-init-output.log

# Runner service status
systemctl status actions-runner

# Runner logs
journalctl -u actions-runner -f
```

### Check 3: Registration Token

```bash
# Check if token was stored
aws secretsmanager list-secrets --filter Key="name",Values="actions-runner"

# Verify token content (will be deleted after use)
```

### Common Fixes

**Invalid GitHub credentials:**
```bash
# For token auth - verify token has admin:org scope
# For App auth - verify App has Self-hosted runners permission
```

**Network issues:**
```hcl
# Ensure Lambda has internet access
lambda_subnet_ids = data.aws_subnets.private.ids  # Must have NAT
```

## Runner Stuck Terminating

### Symptoms
- Instance in `Terminating:Wait` state
- Lifecycle hook not completing

### Check: Deregistration Lambda Logs

```bash
aws logs tail \
  /aws/lambda/$(terraform output -raw autoscaling_group_name)_deregistration \
  --follow
```

### Common Fixes

**SSM command failing:**
```bash
# Check SSM agent status on instance
aws ssm describe-instance-information --filters Key=InstanceIds,Values=i-xxx
```

**Job still running:**
The Lambda waits for `allowed_drain_time` (default 900s) for jobs to complete.

**Force complete lifecycle hook:**
```bash
aws autoscaling complete-lifecycle-action \
  --lifecycle-action-result CONTINUE \
  --lifecycle-hook-name deregistration \
  --auto-scaling-group-name $(terraform output -raw autoscaling_group_name) \
  --instance-id i-xxx
```

## Scaling Issues

### Not Scaling Up

**Check CloudWatch alarm:**
```bash
aws cloudwatch describe-alarms --alarm-names "idle_runners_low-*"
```

**Check record_metric Lambda:**
```bash
aws logs tail \
  /aws/lambda/$(terraform output -raw autoscaling_group_name)_record_metric
```

**Verify metric is being published:**
```bash
aws cloudwatch get-metric-statistics \
  --namespace "InfraHouse/ActionsRunner" \
  --metric-name "IdleRunnersCount" \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Average
```

### Not Scaling Down

**Check for orphaned runners:**
```bash
# List GitHub runners
gh api orgs/{org}/actions/runners --jq '.runners[] | {id, name, status, busy}'

# Compare with ASG instances
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names $(terraform output -raw autoscaling_group_name) \
  --query 'AutoScalingGroups[0].Instances[*].InstanceId'
```

The deregistration Lambda cleans up orphaned runners on schedule.

## Warm Pool Issues

### Instances Not Entering Warm Pool

**Check ASG configuration:**
```bash
aws autoscaling describe-warm-pool \
  --auto-scaling-group-name $(terraform output -raw autoscaling_group_name)
```

**Verify warm pool is enabled:**
```hcl
# Warm pool is disabled when using spot
on_demand_base_capacity = null  # Must be null for warm pool
```

### Instances Not Waking from Warm Pool

**Check instance state:**
```bash
aws ec2 describe-instances --instance-ids i-xxx \
  --query 'Reservations[*].Instances[*].[InstanceId,State.Name]'
```

Hibernated instances should show `stopped`. If they show `running`, they're not properly hibernated.

## Lambda Errors

### Throttling

**Check CloudWatch alarm:**
```bash
aws cloudwatch describe-alarms --alarm-name-prefix "throttles"
```

**Increase concurrency:**
The Lambdas use default concurrency. Consider requesting a limit increase if you see frequent throttling.

### Timeout

**Check Lambda configuration:**
```bash
aws lambda get-function --function-name $(terraform output -raw autoscaling_group_name)_registration \
  --query 'Configuration.Timeout'
```

Registration and deregistration Lambdas have default timeouts. If GitHub API is slow, you may see timeouts.

## Authentication Errors

### "Bad credentials" from GitHub API

**For token auth:**
```bash
# Test token
curl -H "Authorization: token $(aws secretsmanager get-secret-value --secret-id your-secret --query SecretString --output text)" \
  https://api.github.com/orgs/{org}/actions/runners
```

**For App auth:**
```bash
# Check App installation
gh api /orgs/{org}/installations --jq '.installations[] | {app_id, app_slug}'
```

### Rate Limiting

GitHub API limits:
- Personal token: 5,000 requests/hour
- GitHub App: 5,000 requests/hour per installation

**Monitor rate limit:**
```bash
curl -H "Authorization: token xxx" https://api.github.com/rate_limit
```

## Puppet Failures

### Check Puppet Logs

```bash
# On the instance
cat /var/log/puppet.log
journalctl -u puppet
```

### Common Issues

**Hiera config not found:**
```hcl
# Verify path exists
puppet_hiera_config_path = "/opt/puppet-code/environments/production/hiera.yaml"
```

**Module not found:**
```hcl
# Ensure package is installed
packages = ["infrahouse-puppet-data"]
```

## Getting Help

### Collect Debug Information

```bash
# ASG status
aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names $(terraform output -raw autoscaling_group_name)

# Recent Lambda logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/$(terraform output -raw autoscaling_group_name)_registration \
  --start-time $(date -u -d '1 hour ago' +%s000)

# GitHub runner status
gh api orgs/{org}/actions/runners
```

### [Open an Issue](https://github.com/infrahouse/terraform-aws-actions-runner/issues/new)

Include:

- Module version
- Terraform plan/apply output
- Lambda logs
- Instance cloud-init logs (if applicable)
