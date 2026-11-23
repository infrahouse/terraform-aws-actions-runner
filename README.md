# terraform-aws-actions-runner

Self-hosted GitHub Actions runners on an EC2 Auto Scaling Group, with a hibernated warm pool.

[![Need Help?](https://img.shields.io/badge/Need%20Help%3F-Contact%20Us-0066CC)](https://infrahouse.com/contact)
[![Docs](https://img.shields.io/badge/docs-github.io-blue)](https://infrahouse.github.io/terraform-aws-actions-runner/)
[![Registry](https://img.shields.io/badge/Terraform-Registry-purple?logo=terraform)](https://registry.terraform.io/modules/infrahouse/actions-runner/aws/latest)
[![Release](https://img.shields.io/github/release/infrahouse/terraform-aws-actions-runner.svg)](https://github.com/infrahouse/terraform-aws-actions-runner/releases/latest)
[![AWS EC2](https://img.shields.io/badge/AWS-EC2-orange?logo=amazonec2)](https://aws.amazon.com/ec2/)
[![AWS Auto Scaling](https://img.shields.io/badge/AWS-Auto%20Scaling-orange?logo=amazonec2)](https://aws.amazon.com/autoscaling/)
[![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-orange?logo=awslambda)](https://aws.amazon.com/lambda/)
[![Security](https://img.shields.io/github/actions/workflow/status/infrahouse/terraform-aws-actions-runner/vuln-scanner-pr.yml?label=Security)](https://github.com/infrahouse/terraform-aws-actions-runner/actions/workflows/vuln-scanner-pr.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

This Terraform module creates an Auto Scaling Group (ASG) and registers its instances
as [self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners)
for GitHub Actions. It supports authentication using either a
classic GitHub token or temporary tokens obtained via a GitHub App.

> Note: The module registers organization-level runners only even though GitHub supports repository-specific runners.

See [Why Self-Hosted Runners?](https://infrahouse.github.io/terraform-aws-actions-runner/#why-self-hosted-runners)
for what running your own runners gives you over GitHub-hosted ones.

## Why This Module?

The runners are an ordinary EC2 Auto Scaling Group, and the module leaves scaling, health checks and replacement to
AWS primitives you can inspect with the usual tools:

- **Scaling you can read in CloudWatch.** A Lambda function publishes the pool's busy and idle runner counts every
  minute, and two CloudWatch alarms on the idle count drive the ASG's scaling policies. Nothing listens for GitHub
  webhooks, so there is no webhook endpoint to expose or secure.
- **Runners that start in seconds.** The warm pool keeps instances that have already booted and run Puppet in a
  hibernated state. Scaling out wakes one up instead of building a new instance from scratch.
- **Jobs that finish.** A runner protects itself from scale-in for the length of every job, the termination
  lifecycle hook waits for the running job before it deregisters the runner, and an instance refresh waits for busy
  runners instead of replacing them mid-job.
- **No broken runners in service.** A launch lifecycle hook holds every new instance until cloud-init and Puppet
  finish. If any step fails, the instance is abandoned instead of joining the pool.
- **Configured by Puppet.** Cloud-init runs Puppet's `gha_runner` role on every runner, and you add your own Hiera
  data, packages, files and APT repositories on top.
- **Monitoring included.** Every Lambda function comes with error, throttle and memory alarms, the ASG with health
  and saturation alarms, and the pool with a CloudWatch dashboard. Logs are kept for 365 days by default, as ISO 27001
  and SOC 2 audits expect.

It is not the right fit if you need ephemeral, Windows or repository-level runners, or scaling on GitHub webhook
events. The [comparison](https://infrahouse.github.io/terraform-aws-actions-runner/comparison/) goes through the
trade-offs.

## Features

- **Auto Scaling Group of runners** on the latest Ubuntu AMI for `ubuntu_codename` (Ubuntu Pro `noble` by default),
  or on your own `ami_id`. IMDSv2 is required, the root volume is encrypted, and instances are replaced after
  `max_instance_lifetime_days` (30 by default).
- **Hibernated warm pool**, on by default and sized with `warm_pool_min_size` and `warm_pool_max_size`.
- **Spot instances with an on-demand base**: set `on_demand_base_capacity`, and the capacity above it is spot. The
  warm pool is off in this mode.
- **Autoscaling on idle runners**: the `record_metric` Lambda publishes `BusyRunners` and `IdleRunners` every minute,
  and CloudWatch alarms add or remove `autoscaling_step` instances to keep `idle_runners_target_count` runners idle.
- **GitHub App or classic token authentication**: `github_app_pem_secret_arn` and `github_app_id`, or
  `github_token_secret_arn`, read from AWS Secrets Manager.
- **Registration Lambda** on the launch lifecycle hook: it creates a runner registration token and stores it in
  Secrets Manager for the new instance.
- **Deregistration Lambda** on the termination lifecycle hook, plus a sweep every 30 minutes that removes orphaned
  runners from GitHub.
- **Bootstrap lifecycle hook**: an instance whose cloud-init or Puppet run fails is abandoned and never enters
  service.
- **Scale-in protection while a job runs**, and instance refreshes that wait for protected runners.
- **Puppet configuration through cloud-init**: Hiera and module paths, plus `packages`, `extra_files`, `extra_repos`
  and `post_runcmd`.
- **Runner labels**: `extra_labels` on top of the labels the module adds (`aws_region:…`, `aws_account:…`,
  `installation_id:…`).
- **Alarms and a dashboard**: an SNS topic for `alarm_emails`, fanned out to `alarm_topic_arns`; alarms on CPU, the
  ASG at or saturated at its maximum, no instances in service, stuck launches, an empty warm pool and instances that
  never registered; error, throttle and memory alarms on every Lambda function (via terraform-aws-lambda-monitored);
  and a CloudWatch dashboard for the pool.
- **Predictable IAM role names**: `actions-runner-instance-<pool>` and `actions-runner-lambda-<pool>-…`, with
  outputs for writing policies against them.
- **AWS provider 5 and 6** (`>= 5.31, < 7.0`).

## Quick Start

The module needs a Secrets Manager secret that holds either a classic GitHub token with the `admin:org` scope or the
private key of a GitHub App (see
[Authentication](https://infrahouse.github.io/terraform-aws-actions-runner/authentication/)). With a token:

```hcl
module "actions_runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "6.0.0"

  environment             = "production"
  github_org_name         = "your-org"
  subnet_ids              = ["subnet-0123456789abcdef0", "subnet-0fedcba9876543210"]
  alarm_emails            = ["oncall@example.com"]
  github_token_secret_arn = "arn:aws:secretsmanager:us-west-2:123456789012:secret:GITHUB_TOKEN-AbCdEf"
}
```

Either `github_token_secret_arn` or `github_app_pem_secret_arn` is required. To authenticate as a GitHub App, set
`github_app_pem_secret_arn` and `github_app_id` instead of `github_token_secret_arn`.

Puppet configures the runners, and its `profile::github_runner` reads the organization from Hiera
(`profile::github_runner::url` and `profile::github_runner::org`). Provide them in your Hiera data, for instance in a
package listed in `packages`, and point `puppet_hiera_config_path` at its `hiera.yaml`, as the
[examples](examples/) do. They are complete root modules for both kinds of authentication.

## Documentation

The full documentation is on [GitHub Pages](https://infrahouse.github.io/terraform-aws-actions-runner/):

- [Getting Started](https://infrahouse.github.io/terraform-aws-actions-runner/getting-started/): prerequisites and
  the first deployment
- [Architecture](https://infrahouse.github.io/terraform-aws-actions-runner/architecture/): how the module works
- [Configuration](https://infrahouse.github.io/terraform-aws-actions-runner/configuration/): every input
- [Authentication](https://infrahouse.github.io/terraform-aws-actions-runner/authentication/): GitHub App or token
  setup
- [Scaling](https://infrahouse.github.io/terraform-aws-actions-runner/scaling/): warm pool, autoscaling and spot
  instances
- [Monitoring](https://infrahouse.github.io/terraform-aws-actions-runner/monitoring/): alarms, the dashboard and
  Lambda alerting
- [Examples](https://infrahouse.github.io/terraform-aws-actions-runner/examples/): common deployment patterns
- [Troubleshooting](https://infrahouse.github.io/terraform-aws-actions-runner/troubleshooting/): common issues and
  their fixes
- [Upgrading](https://infrahouse.github.io/terraform-aws-actions-runner/upgrading/): what to change when you upgrade,
  and notes on earlier releases
- [Comparison](https://infrahouse.github.io/terraform-aws-actions-runner/comparison/): how this module compares with
  alternatives
- [Changelog](https://infrahouse.github.io/terraform-aws-actions-runner/changelog/): every change, release by release

<!-- BEGIN_TF_DOCS -->

## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | ~> 1.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 5.31, < 7.0 |
| <a name="requirement_null"></a> [null](#requirement\_null) | >= 3.2 |
| <a name="requirement_random"></a> [random](#requirement\_random) | >= 3.5 |
| <a name="requirement_tls"></a> [tls](#requirement\_tls) | >= 4.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 5.31, < 7.0 |
| <a name="provider_random"></a> [random](#provider\_random) | >= 3.5 |
| <a name="provider_tls"></a> [tls](#provider\_tls) | >= 4.0 |

## Modules

| Name | Source | Version |
|------|--------|---------|
| <a name="module_deregistration"></a> [deregistration](#module\_deregistration) | ./modules/runner_deregistration | n/a |
| <a name="module_instance-profile"></a> [instance-profile](#module\_instance-profile) | registry.infrahouse.com/infrahouse/instance-profile/aws | 2.0.0 |
| <a name="module_record_metric"></a> [record\_metric](#module\_record\_metric) | ./modules/record_metric | n/a |
| <a name="module_registration"></a> [registration](#module\_registration) | ./modules/runner_registration | n/a |
| <a name="module_userdata"></a> [userdata](#module\_userdata) | registry.infrahouse.com/infrahouse/cloud-init/aws | 2.4.1 |

## Resources

| Name | Type |
|------|------|
| [aws_autoscaling_group.actions-runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/autoscaling_group) | resource |
| [aws_autoscaling_lifecycle_hook.terminating](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/autoscaling_lifecycle_hook) | resource |
| [aws_autoscaling_policy.scale_in](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/autoscaling_policy) | resource |
| [aws_autoscaling_policy.scale_out](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/autoscaling_policy) | resource |
| [aws_cloudwatch_dashboard.actions_runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_dashboard) | resource |
| [aws_cloudwatch_metric_alarm.asg_at_max](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.asg_launch_stuck](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.asg_saturated_at_max](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.asg_zero_in_service](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.cpu_utilization_alarm](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.idle_runners_high](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.idle_runners_low](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.runner_registration_gap](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.warm_pool_empty](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_iam_policy.required](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_policy) | resource |
| [aws_key_pair.actions-runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/key_pair) | resource |
| [aws_launch_template.actions-runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/launch_template) | resource |
| [aws_security_group.actions-runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/security_group) | resource |
| [aws_sns_topic.alarms](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sns_topic) | resource |
| [aws_sns_topic_subscription.alarm_emails](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/sns_topic_subscription) | resource |
| [aws_vpc_security_group_egress_rule.default](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/vpc_security_group_egress_rule) | resource |
| [aws_vpc_security_group_ingress_rule.icmp](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/vpc_security_group_ingress_rule) | resource |
| [aws_vpc_security_group_ingress_rule.ssh](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/vpc_security_group_ingress_rule) | resource |
| [random_string.asg_name](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/string) | resource |
| [random_string.profile-suffix](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/string) | resource |
| [random_string.reg_token_suffix](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/string) | resource |
| [random_uuid.installation-id](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/uuid) | resource |
| [tls_private_key.actions-runner](https://registry.terraform.io/providers/hashicorp/tls/latest/docs/resources/private_key) | resource |
| [aws_ami.selected](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ami) | data source |
| [aws_ami.ubuntu](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ami) | data source |
| [aws_caller_identity.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/caller_identity) | data source |
| [aws_default_tags.provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/default_tags) | data source |
| [aws_ec2_instance_type.this](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/ec2_instance_type) | data source |
| [aws_iam_policy.ssm](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy) | data source |
| [aws_iam_policy_document.required_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_partition.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/partition) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |
| [aws_subnet.selected](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/subnet) | data source |
| [aws_vpc.selected](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/vpc) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_alarm_emails"></a> [alarm\_emails](#input\_alarm\_emails) | List of email addresses to receive alarm notifications for Lambda function errors. At least one email is required for Lambda error monitoring. | `list(string)` | n/a | yes |
| <a name="input_alarm_topic_arns"></a> [alarm\_topic\_arns](#input\_alarm\_topic\_arns) | List of existing SNS topic ARNs to fan alarm notifications out to (e.g. PagerDuty, Slack, shared org topics). The module always creates its own topic for alarm\_emails; this list is additive. | `list(string)` | `[]` | no |
| <a name="input_allowed_drain_time"></a> [allowed\_drain\_time](#input\_allowed\_drain\_time) | How many seconds to give a running job to finish after the instance fails health checks. Maximum allowed value is 900 seconds. | `number` | `900` | no |
| <a name="input_ami_id"></a> [ami\_id](#input\_ami\_id) | AMI id for EC2 instances. By default, latest Ubuntu var.ubuntu\_codename. | `string` | `null` | no |
| <a name="input_architecture"></a> [architecture](#input\_architecture) | The CPU architecture for the Lambda function; valid values are `x86_64` or `arm64`. | `string` | `"x86_64"` | no |
| <a name="input_asg_max_size"></a> [asg\_max\_size](#input\_asg\_max\_size) | Maximum number of EC2 instances in the ASG. By default, the number of subnets plus one. | `number` | `null` | no |
| <a name="input_asg_min_size"></a> [asg\_min\_size](#input\_asg\_min\_size) | Minimal number of EC2 instances in the ASG. By default, the number of subnets. | `number` | `null` | no |
| <a name="input_autoscaling_scaleout_evaluation_period"></a> [autoscaling\_scaleout\_evaluation\_period](#input\_autoscaling\_scaleout\_evaluation\_period) | The duration, in seconds, that the autoscaling policy will evaluate the scaling conditions before executing a scale-out action. This period helps to prevent unnecessary scaling by allowing time for metrics to stabilize after fluctuations. Default value is 60 seconds. | `number` | `60` | no |
| <a name="input_autoscaling_step"></a> [autoscaling\_step](#input\_autoscaling\_step) | How many instances to add or remove when the autoscaling policy is triggered. | `number` | `1` | no |
| <a name="input_cloudwatch_log_group_retention"></a> [cloudwatch\_log\_group\_retention](#input\_cloudwatch\_log\_group\_retention) | Number of days you want to retain log events in the log group. | `number` | `365` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment name. Passed on as a puppet fact. | `string` | n/a | yes |
| <a name="input_error_rate_threshold"></a> [error\_rate\_threshold](#input\_error\_rate\_threshold) | Error rate threshold percentage for Lambda error alerting. Alerts trigger when error rate exceeds this percentage. | `number` | `10` | no |
| <a name="input_extra_files"></a> [extra\_files](#input\_extra\_files) | Additional files to create on an instance. | <pre>list(<br/>    object(<br/>      {<br/>        content     = string<br/>        path        = string<br/>        permissions = string<br/>      }<br/>    )<br/>  )</pre> | `[]` | no |
| <a name="input_extra_instance_profile_permissions"></a> [extra\_instance\_profile\_permissions](#input\_extra\_instance\_profile\_permissions) | A JSON with a permissions policy document. The policy will be attached to the ASG instance profile. | `string` | `null` | no |
| <a name="input_extra_labels"></a> [extra\_labels](#input\_extra\_labels) | A list of strings to be added as actions runner labels. | `list(string)` | `[]` | no |
| <a name="input_extra_policies"></a> [extra\_policies](#input\_extra\_policies) | A map of additional policy ARNs to attach to the instance role. | `map(string)` | `{}` | no |
| <a name="input_extra_repos"></a> [extra\_repos](#input\_extra\_repos) | Additional APT repositories to configure on an instance. | <pre>map(<br/>    object(<br/>      {<br/>        source   = string<br/>        key      = string<br/>        machine  = optional(string)<br/>        authFrom = optional(string)<br/>        priority = optional(number)<br/>      }<br/>    )<br/>  )</pre> | `{}` | no |
| <a name="input_github_app_id"></a> [github\_app\_id](#input\_github\_app\_id) | GitHub App that gives out GitHub tokens for Terraform. Required if github\_app\_pem\_secret\_arn is not null. For instance, https://github.com/organizations/infrahouse/settings/apps/infrahouse-github-terraform | `number` | `null` | no |
| <a name="input_github_app_pem_secret_arn"></a> [github\_app\_pem\_secret\_arn](#input\_github\_app\_pem\_secret\_arn) | ARN of a secret that stores GitHub App PEM key. Either github\_token\_secret\_arn or github\_app\_pem\_secret\_arn is required. | `string` | `null` | no |
| <a name="input_github_org_name"></a> [github\_org\_name](#input\_github\_org\_name) | GitHub organization name. | `string` | n/a | yes |
| <a name="input_github_token_secret_arn"></a> [github\_token\_secret\_arn](#input\_github\_token\_secret\_arn) | ARN of a secret that stores GitHub token. Either github\_token\_secret\_arn or github\_app\_pem\_secret\_arn is required. | `string` | `null` | no |
| <a name="input_gzip_userdata"></a> [gzip\_userdata](#input\_gzip\_userdata) | Whether to compress user data. Enable if user data exceeds the EC2 16 KB limit (base64-encoded). | `bool` | `false` | no |
| <a name="input_idle_runners_target_count"></a> [idle\_runners\_target\_count](#input\_idle\_runners\_target\_count) | How many idle runners to aim for in the autoscaling policy. | `number` | `1` | no |
| <a name="input_instance_type"></a> [instance\_type](#input\_instance\_type) | EC2 Instance type | `string` | `"t3a.micro"` | no |
| <a name="input_keypair_name"></a> [keypair\_name](#input\_keypair\_name) | SSH key pair name that will be added to the actions runner instance. By default, create and use a new SSH keypair. | `string` | `null` | no |
| <a name="input_lambda_subnet_ids"></a> [lambda\_subnet\_ids](#input\_lambda\_subnet\_ids) | List of subnet IDs where the Lambda functions (runner\_registration, runner\_deregistration, record\_metric) will run.<br/><br/>REQUIREMENTS: The subnets MUST have either:<br/>- NAT Gateway/Instance for internet access to AWS services, OR<br/>- VPC Endpoints for: SSM, Secrets Manager, EC2, AutoScaling, CloudWatch<br/><br/>The Lambda functions need VPC networking to:<br/>- Send SSM commands to EC2 instances (start/stop actions-runner service)<br/>- Access Secrets Manager (GitHub credentials, registration tokens)<br/>- Call EC2/AutoScaling APIs (describe instances, complete lifecycle actions)<br/>- Publish CloudWatch metrics<br/><br/>If not specified, defaults to var.subnet\_ids (runner instance subnets).<br/><br/>WARNING: Lambda functions will fail if subnets lack internet/AWS service access. | `list(string)` | `null` | no |
| <a name="input_max_instance_lifetime_days"></a> [max\_instance\_lifetime\_days](#input\_max\_instance\_lifetime\_days) | The maximum amount of time, in \_days\_, that an instance can be in service, values must be either equal to 0 or between 1 and 365 days. | `number` | `30` | no |
| <a name="input_on_demand_base_capacity"></a> [on\_demand\_base\_capacity](#input\_on\_demand\_base\_capacity) | If specified, the ASG will request spot instances and this will be the minimal number of on-demand instances. Also, warm pool will be disabled. | `number` | `null` | no |
| <a name="input_packages"></a> [packages](#input\_packages) | List of packages to install when the instances bootstraps. | `list(string)` | `[]` | no |
| <a name="input_post_runcmd"></a> [post\_runcmd](#input\_post\_runcmd) | Commands to run after runcmd | `list(string)` | `[]` | no |
| <a name="input_puppet_debug_logging"></a> [puppet\_debug\_logging](#input\_puppet\_debug\_logging) | Enable debug logging if true. | `bool` | `false` | no |
| <a name="input_puppet_environmentpath"></a> [puppet\_environmentpath](#input\_puppet\_environmentpath) | A path for directory environments. | `string` | `"{root_directory}/environments"` | no |
| <a name="input_puppet_hiera_config_path"></a> [puppet\_hiera\_config\_path](#input\_puppet\_hiera\_config\_path) | Path to hiera configuration file. | `string` | `"{root_directory}/environments/{environment}/hiera.yaml"` | no |
| <a name="input_puppet_manifest"></a> [puppet\_manifest](#input\_puppet\_manifest) | Path to puppet manifest. By default ih-puppet will apply {root\_directory}/environments/{environment}/manifests/site.pp. | `string` | `null` | no |
| <a name="input_puppet_module_path"></a> [puppet\_module\_path](#input\_puppet\_module\_path) | Path to common puppet modules. | `string` | `"{root_directory}/environments/{environment}/modules:{root_directory}/modules"` | no |
| <a name="input_puppet_root_directory"></a> [puppet\_root\_directory](#input\_puppet\_root\_directory) | Path where the puppet code is hosted. | `string` | `"/opt/puppet-code"` | no |
| <a name="input_python_version"></a> [python\_version](#input\_python\_version) | Python runtime for the module's Lambda functions: python3.12 or python3.13.<br/>terraform-aws-lambda-monitored packages manylinux\_2\_28 wheels, which only the Amazon Linux 2023<br/>runtimes can load, so earlier runtimes (python3.11 and older, Amazon Linux 2) are not supported.<br/>See https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html | `string` | `"python3.12"` | no |
| <a name="input_root_volume_size"></a> [root\_volume\_size](#input\_root\_volume\_size) | Root volume size in EC2 instance in Gigabytes | `number` | `30` | no |
| <a name="input_subnet_ids"></a> [subnet\_ids](#input\_subnet\_ids) | List of subnet ids where the actions runner instances will be created. | `list(string)` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | A map of tags to add to resources. | `map(string)` | `{}` | no |
| <a name="input_ubuntu_codename"></a> [ubuntu\_codename](#input\_ubuntu\_codename) | Ubuntu version to use for the actions runner. | `string` | `"noble"` | no |
| <a name="input_warm_pool_max_size"></a> [warm\_pool\_max\_size](#input\_warm\_pool\_max\_size) | Max allowed number of instances in the warm pool. By default, same as asg\_max\_size. | `number` | `null` | no |
| <a name="input_warm_pool_min_size"></a> [warm\_pool\_min\_size](#input\_warm\_pool\_min\_size) | How many instances to keep in the warm pool. By default, as many as idle runners count target plus one. | `number` | `null` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_alarm_topic_arn"></a> [alarm\_topic\_arn](#output\_alarm\_topic\_arn) | ARN of the SNS topic this module creates for alarm notifications. alarm\_emails are subscribed to this topic; any ARNs passed in alarm\_topic\_arns receive the same alarms in addition to this one. |
| <a name="output_autoscaling_group_name"></a> [autoscaling\_group\_name](#output\_autoscaling\_group\_name) | Autoscaling group name. |
| <a name="output_dashboard_name"></a> [dashboard\_name](#output\_dashboard\_name) | Name of the CloudWatch dashboard the module creates for this runner pool. |
| <a name="output_dashboard_url"></a> [dashboard\_url](#output\_dashboard\_url) | URL of the CloudWatch dashboard the module creates for this runner pool. |
| <a name="output_deregistration_lambda_name"></a> [deregistration\_lambda\_name](#output\_deregistration\_lambda\_name) | Name of the runner\_deregistration lambda function. |
| <a name="output_deregistration_log_group"></a> [deregistration\_log\_group](#output\_deregistration\_log\_group) | CloudWatch log group name for the deregistration lambda |
| <a name="output_lambda_role_arn_pattern"></a> [lambda\_role\_arn\_pattern](#output\_lambda\_role\_arn\_pattern) | ARN pattern that matches this pool's three Lambda execution roles and none of its other roles,<br/>e.g. for an ArnLike condition on aws:PrincipalArn:<br/>arn:<partition>:iam::<account>:role/actions-runner-lambda-<pool>-* |
| <a name="output_lambda_role_name_prefix"></a> [lambda\_role\_name\_prefix](#output\_lambda\_role\_name\_prefix) | Name prefix shared by this pool's three Lambda execution roles: actions-runner-lambda-<pool>-.<br/>Each role is named <prefix><function>-<suffix>, where <function> is reg, dereg or metric.<br/><pool> stays the same for the life of the pool. Every pool's Lambda roles match actions-runner-lambda-*. |
| <a name="output_record_metric_lambda_name"></a> [record\_metric\_lambda\_name](#output\_record\_metric\_lambda\_name) | Name of the record\_metric lambda function. |
| <a name="output_registration_lambda_name"></a> [registration\_lambda\_name](#output\_registration\_lambda\_name) | Name of the runner\_registration lambda function. |
| <a name="output_registration_token_secret_prefix"></a> [registration\_token\_secret\_prefix](#output\_registration\_token\_secret\_prefix) | The prefix used for storing GitHub Actions runner registration token secrets in AWS Secrets Manager |
| <a name="output_runner_role_arn"></a> [runner\_role\_arn](#output\_runner\_role\_arn) | ARN of the runner instance role, the role the runner EC2 instances use. |
| <a name="output_runner_role_name"></a> [runner\_role\_name](#output\_runner\_role\_name) | Name of the runner instance role: actions-runner-instance-<pool>, with the same <pool> as<br/>lambda\_role\_name\_prefix. It never matches actions-runner-lambda-*, the prefix of the module's Lambda roles. |
<!-- END_TF_DOCS -->

## Examples

- [examples/github-token](examples/github-token): runners that authenticate with a classic GitHub token
- [examples/github-app](examples/github-app): runners that authenticate as a GitHub App

Each is a complete root module, and the test suite runs `terraform init` and `terraform validate` on both against this
checkout. They are based on [test_data/actions-runner](test_data/actions-runner), the root module the integration
test deploys. The [Examples](https://infrahouse.github.io/terraform-aws-actions-runner/examples/) page has more
patterns: spot instances, warm pool sizing, several pools in one account and alarm fan-out.

## Contributing

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability, see
[SECURITY.md](SECURITY.md).

## License

This module is licensed under the Apache License 2.0: see [LICENSE](LICENSE).
