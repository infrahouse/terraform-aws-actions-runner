# Scaling

This guide covers how to configure scaling behavior, warm pools, and spot instances.

## Warm Pool

The warm pool keeps instances in a hibernated state, ready to wake up in seconds.

### How It Works

1. Instances boot fully and configure themselves
2. Instead of terminating on scale-in, they hibernate to the warm pool
3. On scale-out, hibernated instances wake up (~10-30 seconds)
4. Cold launches only happen when warm pool is empty

### Configuration

```hcl
module "actions-runner" {
  # ... required variables ...

  # Keep 2 instances warm, allow up to 5
  warm_pool_min_size = 2
  warm_pool_max_size = 5
  
  # Target 1 idle runner at all times
  idle_runners_target_count = 1
}
```

### Default Behavior

If not specified:

- `warm_pool_min_size` = `idle_runners_target_count + 1`
- `warm_pool_max_size` = `asg_max_size`

### Limitations

!!! warning "Spot Instances"
    Warm pool is **automatically disabled** when using spot instances (`on_demand_base_capacity` is set).
    This is an AWS limitation — spot instances cannot be hibernated.

## Spot Instances

Reduce costs by using spot instances for your runners.

### Configuration

```hcl
module "actions-runner" {
  # ... required variables ...

  # Use spot instances with 1 on-demand as fallback
  on_demand_base_capacity = 1
  
  asg_min_size = 2
  asg_max_size = 10
}
```

This configuration:

- Always keeps 1 on-demand instance (reliability)
- Uses spot for all additional capacity
- Warm pool is disabled

### Cost Savings

Spot instances typically cost 60-90% less than on-demand. For CI/CD workloads that can tolerate interruption, this is ideal.

### Spot Interruption Handling

When AWS reclaims a spot instance:

1. ASG lifecycle hook fires
2. Deregistration Lambda gracefully stops the runner
3. Running job may fail (GitHub will retry on another runner)
4. ASG launches replacement instance

!!! tip "Graceful Drain Time"
    Configure `allowed_drain_time` (default: 900 seconds) to give running jobs time to complete before termination.

## Autoscaling

The module uses CloudWatch alarms to scale based on idle runner count.

### How It Works

```
record_metric Lambda (every minute)
        │
        ▼
Publishes: IdleRunnersCount = N
        │
        ▼
CloudWatch Alarms evaluate:
  - idle_runners_low:  N < target → Scale OUT
  - idle_runners_high: N > target → Scale IN
        │
        ▼
ASG Step Scaling Policy executes
```

### Configuration

```hcl
module "actions-runner" {
  # ... required variables ...

  # Target idle runner count
  idle_runners_target_count = 2
  
  # Add/remove this many instances per scaling action
  autoscaling_step = 1
  
  # Wait this long before evaluating scale-out
  autoscaling_scaleout_evaluation_period = 60
}
```

### Scaling Behavior

| Scenario | Action |
|----------|--------|
| 0 idle runners, target is 2 | Scale out by `autoscaling_step` |
| 5 idle runners, target is 2 | Scale in by `autoscaling_step` |
| 2 idle runners, target is 2 | No action |

### Tuning Tips

**For bursty workloads:**
```hcl
autoscaling_step = 3                          # Add more runners at once
autoscaling_scaleout_evaluation_period = 30   # React faster
idle_runners_target_count = 3                 # Keep more idle
```

**For steady workloads:**
```hcl
autoscaling_step = 1                          # Gradual scaling
autoscaling_scaleout_evaluation_period = 120  # Avoid thrashing
idle_runners_target_count = 1                 # Minimal idle capacity
```

## ASG Sizing

### Basic Configuration

```hcl
module "actions-runner" {
  # Minimum instances (always running)
  asg_min_size = 1
  
  # Maximum instances (cost control)
  asg_max_size = 10
}
```

### Default Behavior

If not specified:

- `asg_min_size` = number of subnets
- `asg_max_size` = number of subnets + 1

### Instance Lifetime

Instances are automatically recycled to pick up updates:

```hcl
# Recycle instances every 30 days (default)
max_instance_lifetime_days = 30

# Disable recycling
max_instance_lifetime_days = 0
```

## Drain Time

When an instance is terminating, give running jobs time to complete:

```hcl
# Allow 15 minutes for jobs to finish (default: 900 seconds)
allowed_drain_time = 900
```

!!! note
    Maximum allowed value is 900 seconds (AWS limitation).

## Example: High-Availability Setup

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "~> 3.2"

  environment     = "production"
  github_org_name = "my-org"
  subnet_ids      = data.aws_subnets.private.ids  # Multiple AZs
  alarm_emails    = ["oncall@example.com"]
  
  github_token_secret_arn = aws_secretsmanager_secret.token.arn

  # Always have runners ready
  asg_min_size              = 2
  asg_max_size              = 20
  idle_runners_target_count = 3
  
  # Fast scaling for CI spikes
  autoscaling_step                       = 2
  autoscaling_scaleout_evaluation_period = 30
  
  # Warm pool for instant availability
  warm_pool_min_size = 3
  warm_pool_max_size = 10
}
```

## Example: Cost-Optimized Setup

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "~> 3.2"

  environment     = "development"
  github_org_name = "my-org"
  subnet_ids      = [data.aws_subnets.private.ids[0]]  # Single AZ
  alarm_emails    = ["dev@example.com"]
  
  github_token_secret_arn = aws_secretsmanager_secret.token.arn

  # Minimal capacity
  asg_min_size              = 0
  asg_max_size              = 5
  idle_runners_target_count = 0
  
  # Use spot instances
  on_demand_base_capacity = 0  # All spot
  
  # Slower scaling (save money)
  autoscaling_step                       = 1
  autoscaling_scaleout_evaluation_period = 120
}
```
