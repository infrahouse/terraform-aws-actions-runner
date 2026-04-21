# Examples

Common deployment patterns for the `terraform-aws-actions-runner` module.

## Minimal: GitHub Token Auth

The smallest viable configuration — a single runner authenticated with a classic GitHub token.

```hcl
module "actions_runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.5.0"

  environment             = "production"
  github_org_name         = "your-org"
  subnet_ids              = ["subnet-abc123", "subnet-def456"]
  alarm_emails            = ["oncall@example.com"]
  github_token_secret_arn = aws_secretsmanager_secret.github_token.arn
}
```

## GitHub App Authentication

Preferred over classic tokens — App credentials scope cleanly to the org and rotate automatically.

```hcl
module "actions_runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.5.0"

  environment              = "production"
  github_org_name          = "your-org"
  subnet_ids               = module.service_network.subnet_private_ids
  alarm_emails             = ["oncall@example.com"]

  github_app_pem_secret_arn = aws_secretsmanager_secret.github_app_pem.arn
  github_app_id             = 123456
}
```

See [Authentication](authentication.md) for how to provision the GitHub App and PEM secret.

## Spot Instances with an On-Demand Floor

Use spot for the elastic tail, keep a small on-demand baseline so at least one runner is always available for critical jobs.

```hcl
module "actions_runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.5.0"

  environment             = "production"
  github_org_name         = "your-org"
  subnet_ids              = module.service_network.subnet_private_ids
  alarm_emails            = ["oncall@example.com"]
  github_token_secret_arn = aws_secretsmanager_secret.github_token.arn

  asg_min_size            = 2
  asg_max_size            = 20
  on_demand_base_capacity = 1
  instance_type           = "t3a.large"
}
```

## Warm Pool for Fast Job Starts

The warm pool keeps hibernated instances ready so newly scheduled jobs don't wait for full EC2 boot. See [Scaling](scaling.md) for how warm pool interacts with autoscaling.

```hcl
module "actions_runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.5.0"

  environment             = "production"
  github_org_name         = "your-org"
  subnet_ids              = module.service_network.subnet_private_ids
  alarm_emails            = ["oncall@example.com"]
  github_token_secret_arn = aws_secretsmanager_secret.github_token.arn

  asg_min_size           = 2
  asg_max_size           = 10
  warm_pool_min_size     = 2
  warm_pool_max_size     = 5
}
```

## Custom Labels and Larger Instances

Add labels so specific workflows can target this runner pool with `runs-on: [self-hosted, docker, terraform]`.

```hcl
module "actions_runner_heavy" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.5.0"

  environment             = "production"
  github_org_name         = "your-org"
  subnet_ids              = module.service_network.subnet_private_ids
  alarm_emails            = ["oncall@example.com"]
  github_token_secret_arn = aws_secretsmanager_secret.github_token.arn

  instance_type = "c6a.4xlarge"
  extra_labels  = ["docker", "terraform", "heavy"]
  asg_min_size  = 0
  asg_max_size  = 8
}
```

## Multiple Pools in One Account

Deploy separate pools for different workload classes by invoking the module multiple times with distinct labels.

```hcl
module "runners_linux_small" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.5.0"

  environment             = "production"
  github_org_name         = "your-org"
  subnet_ids              = module.service_network.subnet_private_ids
  alarm_emails            = ["oncall@example.com"]
  github_token_secret_arn = aws_secretsmanager_secret.github_token.arn

  instance_type = "t3a.medium"
  extra_labels  = ["small"]
}

module "runners_linux_large" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.5.0"

  environment             = "production"
  github_org_name         = "your-org"
  subnet_ids              = module.service_network.subnet_private_ids
  alarm_emails            = ["oncall@example.com"]
  github_token_secret_arn = aws_secretsmanager_secret.github_token.arn

  instance_type = "c6a.2xlarge"
  extra_labels  = ["large"]
}
```

## Fan Out Alarms to PagerDuty / Slack

`alarm_emails` is required and drives the module-owned SNS topic. To route the same alarms to additional destinations, pass existing SNS topic ARNs via `alarm_topic_arns` — every alarm fires to both channels.

```hcl
module "actions_runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.5.0"

  environment             = "production"
  github_org_name         = "your-org"
  subnet_ids              = module.service_network.subnet_private_ids
  github_token_secret_arn = aws_secretsmanager_secret.github_token.arn

  alarm_emails = ["oncall@example.com"]
  alarm_topic_arns = [
    aws_sns_topic.pagerduty_bridge.arn,
    aws_sns_topic.shared_org_alerts.arn,
  ]
}
```

## See Also

- [Getting Started](getting-started.md)
- [Configuration](configuration.md) — full variable reference
- [Scaling](scaling.md) — warm pool and autoscaling tuning
- [Monitoring](monitoring.md) — alarm contract and SNS fan-out
- [Troubleshooting](troubleshooting.md)
