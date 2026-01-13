# Configuration Reference

This page documents all available configuration options.

## Required Variables

| Variable | Description |
|----------|-------------|
| `environment` | Environment name (e.g., "production"). Passed to Puppet as a fact. |
| `github_org_name` | GitHub organization name where runners will be registered. |
| `subnet_ids` | List of subnet IDs for runner instances. |
| `alarm_emails` | List of email addresses for Lambda error alerts. |

Plus one of:

| Variable | Description |
|----------|-------------|
| `github_token_secret_arn` | ARN of secret containing GitHub PAT |
| `github_app_pem_secret_arn` + `github_app_id` | ARN of secret containing GitHub App PEM key and App ID |

## Instance Configuration

### Compute

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `instance_type` | string | `"t3a.micro"` | EC2 instance type |
| `architecture` | string | `"x86_64"` | CPU architecture (`x86_64` or `arm64`) |
| `ami_id` | string | `null` | Custom AMI ID. Defaults to latest Ubuntu. |
| `ubuntu_codename` | string | `"noble"` | Ubuntu version when using default AMI |
| `root_volume_size` | number | `30` | Root volume size in GB |
| `keypair_name` | string | `null` | SSH key pair name. Creates new if not specified. |

### Auto Scaling

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `asg_min_size` | number | `null` | Minimum instances. Default: number of subnets. |
| `asg_max_size` | number | `null` | Maximum instances. Default: subnets + 1. |
| `idle_runners_target_count` | number | `1` | Target idle runner count for scaling. |
| `autoscaling_step` | number | `1` | Instances to add/remove per scaling action. |
| `autoscaling_scaleout_evaluation_period` | number | `60` | Seconds to evaluate before scaling out. |
| `max_instance_lifetime_days` | number | `30` | Max days before instance recycling. 0 to disable. |
| `allowed_drain_time` | number | `900` | Seconds to wait for jobs before termination. Max 900. |

### Warm Pool

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `warm_pool_min_size` | number | `null` | Minimum warm pool instances. Default: `idle_runners_target_count + 1` |
| `warm_pool_max_size` | number | `null` | Maximum warm pool instances. Default: `asg_max_size` |

!!! note
    Warm pool is disabled when `on_demand_base_capacity` is set (spot instances).

### Spot Instances

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `on_demand_base_capacity` | number | `null` | On-demand instances before using spot. Enables spot mode. |

## GitHub Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `github_org_name` | string | required | GitHub organization name |
| `github_token_secret_arn` | string | `null` | ARN of PAT secret |
| `github_app_pem_secret_arn` | string | `null` | ARN of App PEM secret |
| `github_app_id` | number | `null` | GitHub App ID (required with App PEM) |
| `extra_labels` | list(string) | `[]` | Additional runner labels |

## Puppet Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `puppet_hiera_config_path` | string | `"{root_directory}/environments/{environment}/hiera.yaml"` | Path to Hiera config |
| `puppet_module_path` | string | `"{root_directory}/environments/{environment}/modules:{root_directory}/modules"` | Puppet module path |
| `puppet_root_directory` | string | `"/opt/puppet-code"` | Puppet code root |
| `puppet_environmentpath` | string | `"{root_directory}/environments"` | Environment path |
| `puppet_manifest` | string | `null` | Custom manifest path |
| `puppet_debug_logging` | bool | `false` | Enable Puppet debug logging |

## Cloud-init Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `packages` | list(string) | `[]` | APT packages to install |
| `extra_files` | list(object) | `[]` | Additional files to create |
| `extra_repos` | map(object) | `{}` | Additional APT repositories |
| `post_runcmd` | list(string) | `[]` | Commands to run after setup |

### extra_files format

```hcl
extra_files = [
  {
    content     = "file content here"
    path        = "/etc/myconfig"
    permissions = "0644"
  }
]
```

### extra_repos format

```hcl
extra_repos = {
  docker = {
    source = "deb https://download.docker.com/linux/ubuntu noble stable"
    key    = "https://download.docker.com/linux/ubuntu/gpg"
  }
}
```

## Lambda Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `python_version` | string | `"python3.12"` | Lambda Python runtime |
| `lambda_subnet_ids` | list(string) | `null` | Lambda VPC subnets. Default: `subnet_ids` |
| `cloudwatch_log_group_retention` | number | `365` | Log retention in days |
| `error_rate_threshold` | number | `10` | Error rate % for alerting |

## IAM Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `role_name` | string | `"actions-runner"` | IAM role name for instances |
| `extra_policies` | map(string) | `{}` | Additional IAM policy ARNs |
| `extra_instance_profile_permissions` | string | `null` | Additional IAM policy JSON |

## Networking

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `subnet_ids` | list(string) | required | Subnets for runner instances |
| `lambda_subnet_ids` | list(string) | `null` | Subnets for Lambda functions |

## Monitoring

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `alarm_emails` | list(string) | required | Email addresses for alerts |
| `error_rate_threshold` | number | `10` | Error rate % threshold |
| `sns_topic_alarm_arn` | string | `null` | Existing SNS topic for EC2 alarms |

## Tags

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `tags` | map(string) | `{}` | Additional tags for all resources |

## Outputs

| Output | Description |
|--------|-------------|
| `autoscaling_group_name` | ASG name for monitoring queries |
| `deregistration_log_group` | CloudWatch log group for deregistration Lambda |
| `registration_token_secret_prefix` | Prefix for runner registration secrets |
| `runner_role_arn` | IAM role ARN for runner instances |

## Complete Example

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "~> 3.2"

  # Required
  environment             = "production"
  github_org_name         = "my-org"
  subnet_ids              = data.aws_subnets.private.ids
  alarm_emails            = ["oncall@example.com"]
  github_token_secret_arn = aws_secretsmanager_secret.token.arn

  # Instance sizing
  instance_type    = "t3a.large"
  root_volume_size = 100

  # Scaling
  asg_min_size              = 2
  asg_max_size              = 20
  idle_runners_target_count = 3
  warm_pool_min_size        = 3

  # Labels
  extra_labels = ["docker", "terraform", "large"]

  # Packages
  packages = [
    "docker.io",
    "awscli",
    "jq"
  ]

  # Puppet
  puppet_hiera_config_path = "/opt/infrahouse-puppet-data/environments/production/hiera.yaml"

  # Tags
  tags = {
    Team    = "platform"
    Project = "ci-cd"
  }
}
```
