<!-- BEGIN_TF_DOCS -->

## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | ~> 1.5 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 5.31, < 7.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_aws"></a> [aws](#provider\_aws) | >= 5.31, < 7.0 |

## Modules

| Name | Source | Version |
|------|--------|---------|
| <a name="module_lambda_monitored"></a> [lambda\_monitored](#module\_lambda\_monitored) | registry.infrahouse.com/infrahouse/lambda-monitored/aws | 1.0.4 |

## Resources

| Name | Type |
|------|------|
| [aws_cloudwatch_event_rule.scale](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_rule) | resource |
| [aws_cloudwatch_event_target.scale-in-out](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_target) | resource |
| [aws_iam_policy.runner_registration_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/iam_policy) | resource |
| [aws_lambda_permission.allow_cloudwatch_asg_lifecycle_hook](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/lambda_permission) | resource |
| [aws_caller_identity.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/caller_identity) | data source |
| [aws_iam_policy_document.runner_registration_permissions](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |
| [aws_secretsmanager_secret.github](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/secretsmanager_secret) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_alarm_emails"></a> [alarm\_emails](#input\_alarm\_emails) | List of email addresses to receive alarm notifications for Lambda errors. At least one email is required for ISO 27001 compliance. | `list(string)` | n/a | yes |
| <a name="input_architecture"></a> [architecture](#input\_architecture) | The CPU architecture for the Lambda function; valid values are `x86_64` or `arm64`. | `string` | `"x86_64"` | no |
| <a name="input_asg_name"></a> [asg\_name](#input\_asg\_name) | Autoscaling group name to assign this lambda to. | `string` | n/a | yes |
| <a name="input_cloudwatch_log_group_retention"></a> [cloudwatch\_log\_group\_retention](#input\_cloudwatch\_log\_group\_retention) | Number of days you want to retain log events in the log group. | `number` | `365` | no |
| <a name="input_error_rate_threshold"></a> [error\_rate\_threshold](#input\_error\_rate\_threshold) | Error rate threshold percentage for threshold-based alerting. | `number` | `10` | no |
| <a name="input_github_app_id"></a> [github\_app\_id](#input\_github\_app\_id) | GitHub App that gives out GitHub tokens for Terraform. For instance, https://github.com/organizations/infrahouse/settings/apps/infrahouse-github-terraform | `any` | n/a | yes |
| <a name="input_github_credentials"></a> [github\_credentials](#input\_github\_credentials) | A secret and its type to auth in Github. | <pre>object(<br/>    {<br/>      type : string   # Can be either "token" or "pem"<br/>      secret : string # ARN where either is stored<br/>    }<br/>  )</pre> | n/a | yes |
| <a name="input_github_org_name"></a> [github\_org\_name](#input\_github\_org\_name) | GitHub organization name. | `any` | n/a | yes |
| <a name="input_lambda_timeout"></a> [lambda\_timeout](#input\_lambda\_timeout) | Time in seconds to let lambda run. | `number` | `900` | no |
| <a name="input_python_version"></a> [python\_version](#input\_python\_version) | n/a | `string` | `"python3.12"` | no |
| <a name="input_registration_token_secret_prefix"></a> [registration\_token\_secret\_prefix](#input\_registration\_token\_secret\_prefix) | Secret name prefix that will store a registration token | `any` | n/a | yes |
| <a name="input_security_group_ids"></a> [security\_group\_ids](#input\_security\_group\_ids) | List of security group ids where the lambda will be created. | `list(string)` | n/a | yes |
| <a name="input_subnet_ids"></a> [subnet\_ids](#input\_subnet\_ids) | List of subnet ids where the actions runner instances will be created. | `list(string)` | n/a | yes |
| <a name="input_tags"></a> [tags](#input\_tags) | n/a | `any` | n/a | yes |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_lambda_name"></a> [lambda\_name](#output\_lambda\_name) | Lambda function name that (de)registers runners |
<!-- END_TF_DOCS -->