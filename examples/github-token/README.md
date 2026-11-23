# GitHub Token Example

A pool of self-hosted runners for one GitHub organization that authenticate with a classic GitHub token.

## What This Example Creates

- An Auto Scaling Group of `t3a.small` runners (one to two in service) with a hibernated warm pool
- The registration, deregistration and record_metric Lambda functions and their lifecycle hooks
- An SNS topic for alarms, subscribed to `alarm_emails`, and a CloudWatch dashboard

## Prerequisites

- Private subnets with a NAT gateway, or VPC endpoints, so that the runners and the Lambda functions reach GitHub and
  the AWS APIs.
- A Secrets Manager secret whose value is a classic GitHub token with the `admin:org` scope. See
  [Authentication](https://infrahouse.github.io/terraform-aws-actions-runner/authentication/#personal-access-token-classic).
- Puppet Hiera data that sets `profile::github_runner::url` and `profile::github_runner::org` for the `gha_runner`
  role, on the runner by the time Puppet runs, for instance in a package listed in `packages`.
  `puppet_hiera_config_path` points at its `hiera.yaml`.

## Usage

Create a `terraform.tfvars`:

```hcl
region                   = "us-west-2"
environment              = "production"
github_org_name          = "your-org"
subnet_ids               = ["subnet-0123456789abcdef0", "subnet-0fedcba9876543210"]
alarm_emails             = ["oncall@example.com"]
github_token_secret_arn  = "arn:aws:secretsmanager:us-west-2:123456789012:secret:GITHUB_TOKEN-AbCdEf"
puppet_hiera_config_path = "/opt/your-puppet-data/environments/production/hiera.yaml"
packages                 = ["your-puppet-data"]
```

Then apply it:

```bash
terraform init
terraform apply
```

Every address in `alarm_emails` gets a confirmation email from SNS; alarms reach it only after the link in it is
clicked. The runners show up in the organization's settings, under Actions → Runners, with labels such as
`self-hosted`, `Linux` and `aws_region:us-west-2`.

## Outputs

| Name | Description |
|------|-------------|
| `autoscaling_group_name` | Name of the runners' Auto Scaling Group |
| `runner_role_arn` | ARN of the runner instance role |
| `alarm_topic_arn` | ARN of the SNS topic the pool's alarms go to |
| `dashboard_url` | URL of the pool's CloudWatch dashboard |
