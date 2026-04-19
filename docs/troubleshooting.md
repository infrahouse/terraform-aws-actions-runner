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

## Jobs Failing with `SetInstanceProtection` Error

### Symptoms

A workflow job fails immediately, before any user code runs, with output like:

```
A job started hook has been configured by the self-hosted runner administrator
Run '/usr/local/bin/gha_prerun.sh'
2026-01-30 10:31:07,388: ERROR: ... An error occurred (ValidationError) when
calling the SetInstanceProtection operation: The instance i-0f3066deda420ab46
is not in InService or EnteringStandby or Standby.
Error: Process completed with exit code 1.
```

By the time you investigate, the instance ID in the error message usually
no longer exists (`describe-instances` returns `InvalidInstanceID.NotFound`).

### Root cause

Scale-in race. When the ASG picks an idle runner for termination, it sets
the instance to `Terminating:Wait` and fires the deregistration lifecycle
hook. The hook's Lambda needs ~10s (SSM round-trip) to actually stop the
runner service. During that window, GitHub can dispatch a queued job to
the runner's open long-poll. `gha_prerun.sh` then calls
`SetInstanceProtection(protect=true)` on an already-`Terminating:Wait`
instance, AWS rejects it, and the job exits 1.

The "instance ID doesn't exist" is a timing artifact — the instance
terminated cleanly seconds after the error, and EC2 retains terminated
records for only ~1 hour.

### Fix

Upgrade this module to **3.5.0+** and puppet-code to the version that
includes PR #265 (actions-runner graceful scale-in). Together they:

- Tolerate the `Terminating:Wait` state in `gha_prerun.sh` — the job
  proceeds instead of failing.
- Use systemd's graceful-stop to let the in-flight job finish, including
  `gha_postrun.sh`.
- Complete the lifecycle hook via `ExecStopPost` once the runner exits.
- Heartbeat the hook every 10min so long jobs don't time out.

### How to confirm you're affected

Look for **failed workflow jobs** whose logs contain the error above —
that's the user-visible symptom.

As a cross-check, CloudTrail records every failed
`SetInstanceProtection` call. Each failure in CloudTrail that has a
corresponding failed job at the same timestamp is a race hit:

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=SetInstanceProtection \
  --query 'Events[?ErrorCode!=`null`].[EventTime,CloudTrailEvent]' \
  --output text
```

Or via Athena over CloudTrail logs:

```sql
SELECT eventtime, requestparameters, errormessage
FROM cloudtrail_logs.events
WHERE eventname = 'SetInstanceProtection'
  AND errorcode IS NOT NULL
ORDER BY eventtime DESC;
```

### How to verify the fix is working

The fix **does not** stop `SetInstanceProtection` from failing — AWS
will still reject the call on a `Terminating:Wait` instance, and those
rejections still appear in CloudTrail at roughly the same rate as
before. What changes is that the failure no longer kills the job.

After upgrading, expect:

- **Failed jobs with this error go to zero.** GitHub workflow runs
  succeed even when they land in the race window.
- **CloudTrail failures continue.** Don't be alarmed — they confirm
  the race is still occurring, just that we're surviving it.

On a runner that accepts a job during a scale-in window, the host-side
flow is:

1. `gha_prerun.sh` tries `SetInstanceProtection(protect=true)` — AWS
   rejects — script logs `skipping protect, job will proceed` and
   exits 0.
2. Job runs to completion and `gha_postrun.sh` fires normally.
3. `actions-runner.service` exits via SIGTERM (graceful).
4. `ExecStopPost=/usr/local/bin/gha-on-runner-exit.sh` calls
   `ih-aws autoscaling complete --hook deregistration --result CONTINUE`.
5. Instance terminates.

### Finding jobs a specific runner executed

If you observed a runner get scaled in (say `ip-10-1-0-5`) and want to
confirm the jobs it executed completed successfully rather than failed,
query GitHub for every workflow job with that `runner_name`. There's no
org-wide "jobs for runner X" endpoint, so you iterate repos / runs.
Scope with `created=>=...` to avoid walking historical runs.

```bash
RUNNER=ip-10-1-0-5
ORG=your-org
SINCE=2026-04-01

gh repo list "$ORG" --limit 1000 --no-archived \
  --json nameWithOwner -q '.[].nameWithOwner' |
while read repo; do
  gh api --paginate \
    "/repos/${repo}/actions/runs?per_page=100&created=>=${SINCE}" \
    --jq '.workflow_runs[].id' 2>/dev/null |
  while read run_id; do
    gh api "/repos/${repo}/actions/runs/${run_id}/jobs" \
      --jq ".jobs[] | select(.runner_name==\"${RUNNER}\") |
            {repo:\"${repo}\", id, name, conclusion, started_at, html_url}" \
      2>/dev/null
  done
done
```

Each line of output is a JSON object with the job's conclusion (`success`
/ `failure` / `cancelled`) and URL. The jobs survive in GitHub's data
even after the runner's registration is removed from the org.

For a single-repo scope, drop the outer `gh repo list` loop and set
`repo` directly.

See [issue #81](https://github.com/infrahouse/terraform-aws-actions-runner/issues/81)
for the full history.

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
