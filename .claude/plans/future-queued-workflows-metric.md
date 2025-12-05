# Future Enhancement: Queued Workflows Metric

## Overview

Add a **proactive autoscaling metric** by tracking the number of GitHub workflow runs waiting for available runners. This provides a **leading indicator** for capacity needs, enabling faster autoscaling response before developers experience delays.

## Current State

**Existing Metrics (Reactive):**
- `BusyRunners` - Count of runners executing jobs
- `IdleRunners` - Count of runners waiting for jobs

**Current Autoscaling Logic:**
```
If IdleRunners < target → Scale up
```

**Problem:** This is **reactive** - we only scale up after idle runners are exhausted, meaning workflows are already queued and waiting.

## Proposed Enhancement

### New Metric: `QueuedWorkflows`

**What it measures:** Number of workflow runs in `queued` status waiting for self-hosted runners

**Published to:** CloudWatch namespace `GitHubRunners`

**Dimension:** `asg_name` (same as existing metrics)

**Update frequency:** Every 1 minute (same as existing metrics)

## Benefits

### 1. Proactive Autoscaling (Leading Indicator)

**Current (Reactive):**
```
Workflow starts
  ↓
No idle runners available
  ↓
Workflow queues (developer waits)
  ↓
IdleRunners drops to 0
  ↓
Autoscaling triggered
  ↓
New instance launches (~2-3 minutes)
  ↓
Runner becomes available
  ↓
Workflow starts running

Total delay: 2-3+ minutes
```

**With Queue Depth (Proactive):**
```
Workflow starts
  ↓
QueuedWorkflows > 0 detected
  ↓
Immediate autoscaling triggered
  ↓
New instance already launching
  ↓
No idle runners available
  ↓
Workflow queues briefly
  ↓
New runner becomes available
  ↓
Workflow starts running

Total delay: <1 minute (or zero if runner ready)
```

### 2. Improved Autoscaling Policies

**Enhanced Target Tracking:**
```hcl
# Scale up aggressively when queue grows
resource "aws_autoscaling_policy" "queue_based_scaling" {
  name                   = "scale-on-queue-depth"
  autoscaling_group_name = aws_autoscaling_group.runners.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    customized_metric_specification {
      metric_name = "QueuedWorkflows"
      namespace   = "GitHubRunners"
      statistic   = "Average"
    }
    target_value = 0.0  # Keep queue at zero
  }
}
```

**Multi-Metric Autoscaling:**
```hcl
# Combine idle runners + queue depth for smarter scaling
resource "aws_autoscaling_policy" "smart_scaling" {
  # Scale up if:
  # - IdleRunners < 2 OR
  # - QueuedWorkflows > 0

  # Scale down if:
  # - IdleRunners > 3 AND
  # - QueuedWorkflows == 0
}
```

### 3. Better Capacity Planning

**Metrics to track:**
- Peak queue depth during business hours
- Average time workflows spend queued
- Correlation between queue depth and idle runner count

**Insights:**
- "We regularly hit 10+ queued workflows at 9 AM → increase min_size"
- "Queue depth is always zero → we're over-provisioned"
- "Queue spikes during deployments → pre-scale for known events"

### 4. SLA Monitoring & Alerting

```hcl
resource "aws_cloudwatch_metric_alarm" "queue_too_deep" {
  alarm_name          = "github-runners-queue-depth-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "QueuedWorkflows"
  namespace           = "GitHubRunners"
  period              = 60
  statistic           = "Average"
  threshold           = 5
  alarm_description   = "Alert when >5 workflows waiting for runners"
  alarm_actions       = [aws_sns_topic.ops_alerts.arn]
}
```

## Implementation Plan

### Phase 1: Metric Collection

**Update `modules/record_metric/lambda/main.py`:**

```python
def lambda_handler(event, context):
    # Existing code...
    github = GitHubAuth(get_github_token(), org_name)
    gha = GitHubActions(github)

    # 1. Count busy/idle runners (existing)
    status_counts = Counter()
    for runner in gha.find_runners_by_label(f"installation_id:{installation_id}"):
        if runner.status == "online":
            status_counts["busy" if runner.busy else "idle"] += 1

    # 2. NEW: Count queued workflows waiting for self-hosted runners
    queued_count = _count_queued_workflows(
        github,
        org_name,
        installation_id,
        runner_labels=get_runner_labels()
    )

    # 3. Publish all metrics
    cloudwatch.put_metric_data(
        Namespace="GitHubRunners",
        MetricData=[
            {"MetricName": "BusyRunners", "Value": status_counts["busy"]},
            {"MetricName": "IdleRunners", "Value": status_counts["idle"]},
            {"MetricName": "QueuedWorkflows", "Value": queued_count},  # NEW
        ]
    )


def _count_queued_workflows(github, org, installation_id, runner_labels):
    """
    Count workflow runs that are queued and waiting for self-hosted runners.

    Strategy:
    1. Get all queued workflow runs for the organization
    2. Filter to runs that target self-hosted runners
    3. Match based on runner labels or installation_id
    """
    queued_count = 0

    # Get queued workflow runs
    for run in github.list_workflow_runs(status="queued"):
        # Check if workflow targets self-hosted runners
        if _targets_self_hosted_runners(run, runner_labels, installation_id):
            queued_count += 1

    return queued_count


def _targets_self_hosted_runners(workflow_run, runner_labels, installation_id):
    """
    Determine if a workflow run targets self-hosted runners.

    Methods (in order of preference):
    1. Check workflow file for runs-on labels
    2. Check if run is assigned to a runner group
    3. Heuristic: assume queued runs without GitHub-hosted labels are self-hosted
    """
    # Method 1: Parse workflow file (most accurate but expensive)
    # workflow_file = github.get_workflow_file(workflow_run.workflow_id)
    # if "self-hosted" in workflow_file.runs_on:
    #     return True

    # Method 2: Check runner group (if available via API)
    # if workflow_run.runner_group == "self-hosted":
    #     return True

    # Method 3: Simple heuristic - if not GitHub-hosted, assume self-hosted
    # GitHub-hosted runner labels: ubuntu-latest, windows-latest, macos-latest, etc.
    github_hosted_labels = ["ubuntu-", "windows-", "macos-"]

    # This requires workflow run to expose labels (API limitation)
    # May need to fetch workflow file and parse YAML

    # For MVP: Count ALL queued runs (conservative)
    # Future: Implement label-based filtering
    return True  # Conservative: count all queued runs
```

### Phase 2: IAM Permissions

**Add to `modules/record_metric/main.tf`:**

No additional permissions needed! The Lambda already has:
- `secretsmanager:GetSecretValue` (GitHub credentials) ✅
- `cloudwatch:PutMetricData` ✅

GitHub API calls don't require additional AWS permissions.

### Phase 3: Variables & Configuration

**Optional new variables in `modules/record_metric/variables.tf`:**

```hcl
variable "enable_queue_metrics" {
  description = "Enable tracking of queued workflow runs waiting for runners"
  type        = bool
  default     = true  # Enable by default in future version
}

variable "runner_labels" {
  description = "List of runner labels to match when filtering queued workflows"
  type        = list(string)
  default     = ["self-hosted"]  # Default to all self-hosted runners
}
```

### Phase 4: Testing & Validation

**Test scenarios:**
1. **No queued workflows** → metric should be 0
2. **Workflows queued for self-hosted runners** → metric should increment
3. **Workflows queued for GitHub-hosted runners** → metric should NOT increment
4. **High queue depth (>10)** → verify metric accuracy

**Validation:**
- Compare metric against GitHub UI workflow queue
- Verify autoscaling triggers correctly
- Check for false positives (counting GitHub-hosted runners)

## API Considerations

### GitHub API Rate Limits

**Current usage (record_metric):**
- 1 API call/minute: `GET /orgs/{org}/actions/runners` (list runners)
- ~1,440 calls/day

**With queue depth:**
- 1 API call/minute: `GET /orgs/{org}/actions/runners` (existing)
- 1 API call/minute: `GET /orgs/{org}/actions/runs?status=queued` (new)
- ~2,880 calls/day total

**Rate limits:**
- GitHub Enterprise Cloud: 5,000 requests/hour
- Usage with queue depth: ~48 requests/hour (< 1% of limit)
- **Impact:** Negligible ✅

### Filtering Challenges

**Problem:** GitHub API doesn't directly expose "which runner this workflow is waiting for"

**Workarounds (in order of preference):**

1. **Label-based filtering** (most accurate)
   - Fetch workflow file
   - Parse `runs-on:` labels
   - Match against runner labels
   - **Trade-off:** Extra API calls + YAML parsing

2. **Runner group filtering** (if available)
   - Use runner group assignment
   - **Trade-off:** Not all API versions expose this

3. **Conservative counting** (simplest, MVP approach)
   - Count ALL queued workflow runs
   - Over-estimate is better than under-estimate for autoscaling
   - **Trade-off:** May scale up unnecessarily for GitHub-hosted runners

**Recommended for MVP:** Conservative counting (option 3)
- Simpler implementation
- No extra API calls
- Safe for autoscaling (better to over-provision than under-provision)
- Can refine in future versions

## Rollout Strategy

### Version 1.x (Future Minor Release)

**Scope:** Add metric collection only
- Add `QueuedWorkflows` metric to CloudWatch
- Conservative counting (all queued runs)
- Document in README
- No autoscaling policy changes (let users experiment)

**Breaking changes:** None (additive change)

### Version 2.x (Future Major Release)

**Scope:** Enhanced filtering + autoscaling policies
- Label-based filtering for accurate counts
- Example autoscaling policies in documentation
- Optional: pre-built autoscaling modules
- CloudWatch dashboard examples

**Breaking changes:** Potentially change default autoscaling behavior

## Success Metrics

**To measure effectiveness:**
1. **Reduced workflow queue time**
   - Before: Average time in queue
   - After: Average time in queue
   - Target: 50% reduction

2. **Faster autoscaling response**
   - Before: Time from "no idle runners" to "new runner available"
   - After: Time from "queue detected" to "new runner available"
   - Target: 30 seconds faster

3. **Better capacity utilization**
   - Before: Idle runner count variance
   - After: Idle runner count variance
   - Target: More stable, fewer spikes

## Documentation Updates

### README.md additions

**New section: "Queued Workflows Metric"**
```markdown
### QueuedWorkflows Metric

Tracks the number of workflow runs waiting for available runners.

**Usage:**
- Proactive autoscaling (scale before workflows wait)
- SLA monitoring (alert on queue depth)
- Capacity planning (understand peak demand)

**Autoscaling example:**
[Include example policy]
```

### Migration guide

**For users upgrading:**
- New metric automatically enabled
- No action required
- Optional: Update autoscaling policies to use queue depth

## Future Enhancements (Beyond Queue Depth)

### Additional Metrics to Consider:

1. **`QueuedWorkflowsAge`** - How long workflows have been waiting
2. **`RunnerStartupTime`** - Time from instance launch to runner ready
3. **`WorkflowWaitTime`** - Time from workflow start to runner assignment
4. **`RunnerUtilization`** - Percentage of time runners are busy (over time window)

### Advanced Autoscaling:

1. **Predictive scaling** based on time-of-day patterns
2. **Event-based pre-scaling** (scale before deployments)
3. **Cost optimization** (prefer spot instances during low priority)

## References

- [GitHub Actions REST API - Workflow Runs](https://docs.github.com/en/rest/actions/workflow-runs)
- [GitHub Actions REST API - Self-hosted Runners](https://docs.github.com/en/rest/actions/self-hosted-runners)
- [AWS CloudWatch Custom Metrics](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/publishingMetrics.html)
- [AWS Auto Scaling Target Tracking Policies](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-scaling-target-tracking.html)

## Related Issues/PRs

_To be created when implementing:_
- [ ] Issue: Add QueuedWorkflows metric for proactive autoscaling
- [ ] PR: Implement queued workflow tracking in record_metric module
- [ ] PR: Add autoscaling policy examples using queue depth
- [ ] PR: Update documentation with queue depth best practices

---

**Created:** 2025-12-04
**Status:** Planning / Not Yet Implemented
**Priority:** Medium (Quality of Life improvement)
**Estimated Effort:** 1-2 days (development + testing)