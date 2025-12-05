# terraform-aws-actions-runner

This Terraform module creates an Auto Scaling Group (ASG) and registers its instances
as self-hosted runners for GitHub Actions. It supports authentication using either a
classic GitHub token or temporary tokens obtained via a GitHub App.

## Why Self-Hosted Runners?

Self-hosted runners offer several advantages over GitHub-hosted runners:

- **Cost Efficiency:** Run workflows on your own infrastructure, potentially reducing costs for high-volume CI/CD pipelines, especially when using spot instances.
- **Custom Hardware:** Use specific CPU architectures, GPUs, or other specialized hardware required by your workloads.
- **Access to Private Resources:** Runners deployed in your VPC can access private databases, internal APIs, and other resources without exposing them to the internet.
- **Larger Compute Resources:** Go beyond GitHub-hosted runner limits with custom instance types that have more CPU, memory, or storage.
- **Compliance and Security:** Keep sensitive data and build artifacts within your own infrastructure to meet regulatory requirements.
- **Caching and Performance:** Leverage persistent storage and network proximity to your resources for faster build times.

> Note: The module registers organization-level runners only even though GitHub supports repository-specific runners.

## What's New

- **Migrated runner_deregistration lambda to terraform-aws-lambda-monitored module (v1.0.4):**
    - Automated dependency packaging for Lambda functions (no more custom package.sh scripts)
    - Built-in error monitoring and alerting via SNS
    - Standardized CloudWatch integration with configurable error rate thresholds
    - Scheduled sweep interval changed from 5 to 30 minutes for improved efficiency
    - **Migration is seamless** - existing log groups and data are preserved via `moved` blocks
- **Migrated record_metric lambda to terraform-aws-lambda-monitored module (v1.0.0):**
    - Automated dependency packaging for Lambda functions (no more custom package.sh scripts)
    - Built-in error monitoring and alerting via SNS for Lambda functions
    - Standardized CloudWatch integration with configurable error rate thresholds
    - **Breaking:** Removed `lambda_bucket_name` variable - the module now creates its own S3 bucket for Lambda packages
    - **New Required:** `alarm_emails` variable - list of email addresses for Lambda error notifications (required for ISO 27001 compliance)
    - **New Optional:** `error_rate_threshold` variable - error rate percentage threshold for alerting (default: 10.0)
- **AWS Provider 5 and 6 Support:** The module now supports both AWS provider version 5 and 6, ensuring compatibility across major versions.
- **Enhanced Input Validation:** Added validation blocks for `architecture`, `max_instance_lifetime_days`, and other critical variables to catch configuration errors early.
- **Improved Type Safety:** All variables now have explicit type declarations for better consistency and error prevention.
- **Spot Instances Support:** The module supports spot instances by enabling you to specify the minimum on-demand capacity via the `on_demand_base_capacity` variable.
- **Warm Pool Support:** The module now supports a warm pool, allowing you to pre-initialize instances.
- **Bugfixes:**
    - Improved instance lifecycle management.
    - Prevent action runner jobs from being scheduled on instances going back to the warm pool.
    - Orphaned runners are now automatically deregistered.

## Migration Guide

### Upgrading to v3.1.0+ (runner_deregistration Migration)

The v3.1.0 release migrates the `runner_deregistration` lambda to use `terraform-aws-lambda-monitored` for improved monitoring. 
**This is a seamless upgrade with no breaking changes and no data loss.**

#### What Changed

- Internal implementation now uses `terraform-aws-lambda-monitored` module
- Scheduled sweep interval optimized from 5 minutes to 30 minutes
- New SNS alerts for Lambda errors (using existing `alarm_emails` variable)
- **No configuration changes required** - all variables remain the same

#### Migration Steps

Simply update your module version and apply:

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "~> 3.1"  # Update from 3.0.x

  # All existing configuration remains unchanged
}
```

Then run:

```bash
terraform plan  # Shows resource moves via "moved" blocks
terraform apply # Applies seamlessly
```

**What to expect:**
- Terraform shows resources moving to new paths (not destroy/create)
- CloudWatch logs and data are fully preserved
- Zero downtime - runners continue operating normally
- New monitoring capabilities added automatically

**Verification:** After upgrade, check CloudWatch Logs - your deregistration logs will still be there with full history intact.

## Usage

Below is an example Terraform configuration showing the updated usage:

To make it work, the module needs a secret storing a GitHub classic token with org:admin permissions. 
Alternatively, the module can use temporary GitHub tokens generated by a GitHub App. 

Either `github_token_secret_arn` or `github_app_pem_secret_arn` is required.

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.1.1"

  asg_min_size             = 1
  asg_max_size             = 1
  subnet_ids               = var.subnet_private_ids
  environment              = local.environment
  github_org_name          = "infrahouse"

  # Option 1: Use GitHub classic token
  github_token_secret_arn  = "arn:aws:secretsmanager:us-west-1:123456789:secret:GITHUB_TOKEN-xyz"

  # Option 2: Use GitHub App (comment out Option 1 if using this)
  # github_app_pem_secret_arn = "arn:aws:secretsmanager:us-west-1:123456789:secret:action-runner-pem-xyz"
  # github_app_id             = 123456

  keypair_name             = aws_key_pair.jumphost.key_name
  puppet_hiera_config_path = "/opt/infrahouse-puppet-data/environments/${local.environment}/hiera.yaml"
  packages = [
    "infrahouse-puppet-data"
  ]
  extra_labels = ["awesome"]
}
```

> It's not a bad idea to check `test_data/actions-runner/main.tf` and other files in the directory. 
> They're a part of Terraform unit test and are supposed to work.

---

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
| <a name="module_instance-profile"></a> [instance-profile](#module\_instance-profile) | registry.infrahouse.com/infrahouse/instance-profile/aws | 1.9.0 |
| <a name="module_record_metric"></a> [record\_metric](#module\_record\_metric) | ./modules/record_metric | n/a |
| <a name="module_registration"></a> [registration](#module\_registration) | ./modules/runner_registration | n/a |
| <a name="module_userdata"></a> [userdata](#module\_userdata) | registry.infrahouse.com/infrahouse/cloud-init/aws | 2.2.2 |

## Resources

| Name | Type |
|------|------|
| [aws_autoscaling_group.actions-runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/autoscaling_group) | resource |
| [aws_autoscaling_lifecycle_hook.terminating](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/autoscaling_lifecycle_hook) | resource |
| [aws_autoscaling_policy.scale_in](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/autoscaling_policy) | resource |
| [aws_autoscaling_policy.scale_out](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/autoscaling_policy) | resource |
| [aws_cloudwatch_metric_alarm.cpu_utilization_alarm](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.idle_runners_high](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_cloudwatch_metric_alarm.idle_runners_low](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [aws_iam_policy.required](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_policy) | resource |
| [aws_key_pair.actions-runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/key_pair) | resource |
| [aws_launch_template.actions-runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/launch_template) | resource |
| [aws_s3_bucket.lambda_tmp](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket) | resource |
| [aws_s3_bucket_policy.lambda_tmp](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_policy) | resource |
| [aws_s3_bucket_public_access_block.public_access](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_public_access_block) | resource |
| [aws_security_group.actions-runner](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/security_group) | resource |
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
| [aws_iam_policy.ssm](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy) | data source |
| [aws_iam_policy_document.bucket_policy](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_iam_policy_document.required_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |
| [aws_subnet.selected](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/subnet) | data source |
| [aws_vpc.selected](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/vpc) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_alarm_emails"></a> [alarm\_emails](#input\_alarm\_emails) | List of email addresses to receive alarm notifications for Lambda function errors. At least one email is required for ISO 27001 compliance. | `list(string)` | n/a | yes |
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
| <a name="input_idle_runners_target_count"></a> [idle\_runners\_target\_count](#input\_idle\_runners\_target\_count) | How many idle runners to aim for in the autoscaling policy. | `number` | `1` | no |
| <a name="input_instance_type"></a> [instance\_type](#input\_instance\_type) | EC2 Instance type | `string` | `"t3a.micro"` | no |
| <a name="input_keypair_name"></a> [keypair\_name](#input\_keypair\_name) | SSH key pair name that will be added to the actions runner instance. By default, create and use a new SSH keypair. | `string` | `null` | no |
| <a name="input_lambda_subnet_ids"></a> [lambda\_subnet\_ids](#input\_lambda\_subnet\_ids) | List of subnet IDs where the Lambda functions (runner\_registration, runner\_deregistration, record\_metric) will run.<br/><br/>REQUIREMENTS: The subnets MUST have either:<br/>- NAT Gateway/Instance for internet access to AWS services, OR<br/>- VPC Endpoints for: SSM, Secrets Manager, EC2, AutoScaling, CloudWatch<br/><br/>The Lambda functions need VPC networking to:<br/>- Send SSM commands to EC2 instances (start/stop actions-runner service)<br/>- Access Secrets Manager (GitHub credentials, registration tokens)<br/>- Call EC2/AutoScaling APIs (describe instances, complete lifecycle actions)<br/>- Publish CloudWatch metrics<br/><br/>If not specified, defaults to var.subnet\_ids (runner instance subnets).<br/><br/>WARNING: Lambda functions will fail if subnets lack internet/AWS service access. | `list(string)` | `null` | no |
| <a name="input_max_instance_lifetime_days"></a> [max\_instance\_lifetime\_days](#input\_max\_instance\_lifetime\_days) | The maximum amount of time, in \_days\_, that an instance can be in service, values must be either equal to 0 or between 7 and 365 days. | `number` | `30` | no |
| <a name="input_on_demand_base_capacity"></a> [on\_demand\_base\_capacity](#input\_on\_demand\_base\_capacity) | If specified, the ASG will request spot instances and this will be the minimal number of on-demand instances. Also, warm pool will be disabled. | `number` | `null` | no |
| <a name="input_packages"></a> [packages](#input\_packages) | List of packages to install when the instances bootstraps. | `list(string)` | `[]` | no |
| <a name="input_post_runcmd"></a> [post\_runcmd](#input\_post\_runcmd) | Commands to run after runcmd | `list(string)` | `[]` | no |
| <a name="input_puppet_debug_logging"></a> [puppet\_debug\_logging](#input\_puppet\_debug\_logging) | Enable debug logging if true. | `bool` | `false` | no |
| <a name="input_puppet_environmentpath"></a> [puppet\_environmentpath](#input\_puppet\_environmentpath) | A path for directory environments. | `string` | `"{root_directory}/environments"` | no |
| <a name="input_puppet_hiera_config_path"></a> [puppet\_hiera\_config\_path](#input\_puppet\_hiera\_config\_path) | Path to hiera configuration file. | `string` | `"{root_directory}/environments/{environment}/hiera.yaml"` | no |
| <a name="input_puppet_manifest"></a> [puppet\_manifest](#input\_puppet\_manifest) | Path to puppet manifest. By default ih-puppet will apply {root\_directory}/environments/{environment}/manifests/site.pp. | `string` | `null` | no |
| <a name="input_puppet_module_path"></a> [puppet\_module\_path](#input\_puppet\_module\_path) | Path to common puppet modules. | `string` | `"{root_directory}/environments/{environment}/modules:{root_directory}/modules"` | no |
| <a name="input_puppet_root_directory"></a> [puppet\_root\_directory](#input\_puppet\_root\_directory) | Path where the puppet code is hosted. | `string` | `"/opt/puppet-code"` | no |
| <a name="input_python_version"></a> [python\_version](#input\_python\_version) | Python version to run lambda on. Must be one of https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html | `string` | `"python3.12"` | no |
| <a name="input_role_name"></a> [role\_name](#input\_role\_name) | IAM role name that will be created and used by EC2 instances | `string` | `"actions-runner"` | no |
| <a name="input_root_volume_size"></a> [root\_volume\_size](#input\_root\_volume\_size) | Root volume size in EC2 instance in Gigabytes | `number` | `30` | no |
| <a name="input_sns_topic_alarm_arn"></a> [sns\_topic\_alarm\_arn](#input\_sns\_topic\_alarm\_arn) | ARN of SNS topic for Cloudwatch alarms on base EC2 instance. | `string` | `null` | no |
| <a name="input_subnet_ids"></a> [subnet\_ids](#input\_subnet\_ids) | List of subnet ids where the actions runner instances will be created. | `list(string)` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | A map of tags to add to resources. | `map(string)` | `{}` | no |
| <a name="input_ubuntu_codename"></a> [ubuntu\_codename](#input\_ubuntu\_codename) | Ubuntu version to use for the actions runner. | `string` | `"noble"` | no |
| <a name="input_warm_pool_max_size"></a> [warm\_pool\_max\_size](#input\_warm\_pool\_max\_size) | Max allowed number of instances in the warm pool. By default, as many as idle runners count target plus one. | `number` | `null` | no |
| <a name="input_warm_pool_min_size"></a> [warm\_pool\_min\_size](#input\_warm\_pool\_min\_size) | How many instances to keep in the warm pool. By default, as many as idle runners count target plus one. | `number` | `null` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_autoscaling_group_name"></a> [autoscaling\_group\_name](#output\_autoscaling\_group\_name) | Autoscaling group name. |
| <a name="output_deregistration_log_group"></a> [deregistration\_log\_group](#output\_deregistration\_log\_group) | CloudWatch log group name for the deregistration lambda |
| <a name="output_registration_token_secret_prefix"></a> [registration\_token\_secret\_prefix](#output\_registration\_token\_secret\_prefix) | The prefix used for storing GitHub Actions runner registration token secrets in AWS Secrets Manager |
| <a name="output_runner_role_arn"></a> [runner\_role\_arn](#output\_runner\_role\_arn) | An actions runner EC2 instance role ARN. |
<!-- END_TF_DOCS -->
