variable "allowed_drain_time" {
  description = "How many seconds to give a running job to finish after the instance fails health checks. Maximum allowed value is 900 seconds."
  type        = number
  default     = 900
}

variable "ami_id" {
  description = "AMI id for EC2 instances. By default, latest Ubuntu var.ubuntu_codename."
  type        = string
  default     = null
}

variable "architecture" {
  description = "The CPU architecture for the Lambda function; valid values are `x86_64` or `arm64`."
  type        = string
  default     = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "The architecture must be either 'x86_64' or 'arm64'."
  }
}

variable "asg_min_size" {
  description = "Minimal number of EC2 instances in the ASG. By default, the number of subnets."
  type        = number
  default     = null
}

variable "asg_max_size" {
  description = "Maximum number of EC2 instances in the ASG. By default, the number of subnets plus one."
  type        = number
  default     = null
}

variable "autoscaling_step" {
  description = "How many instances to add or remove when the autoscaling policy is triggered."
  type        = number
  default     = 1
}

variable "autoscaling_scaleout_evaluation_period" {
  description = "The duration, in seconds, that the autoscaling policy will evaluate the scaling conditions before executing a scale-out action. This period helps to prevent unnecessary scaling by allowing time for metrics to stabilize after fluctuations. Default value is 60 seconds."
  type        = number
  default     = 60
}

variable "cloudwatch_log_group_retention" {
  description = "Number of days you want to retain log events in the log group."
  type        = number
  default     = 365
}

variable "alarm_emails" {
  description = "List of email addresses to receive alarm notifications for Lambda function errors. At least one email is required for ISO 27001 compliance."
  type        = list(string)
  validation {
    condition     = length(var.alarm_emails) > 0
    error_message = "At least one alarm email address must be provided for monitoring compliance"
  }
}

variable "error_rate_threshold" {
  description = "Error rate threshold percentage for Lambda error alerting. Alerts trigger when error rate exceeds this percentage."
  type        = number
  default     = 10.0
  validation {
    condition     = var.error_rate_threshold > 0 && var.error_rate_threshold <= 100
    error_message = "error_rate_threshold must be between 0 and 100"
  }
}

variable "environment" {
  description = "Environment name. Passed on as a puppet fact."
  type        = string
}

variable "extra_files" {
  description = "Additional files to create on an instance."
  type = list(
    object(
      {
        content     = string
        path        = string
        permissions = string
      }
    )
  )
  default = []
}

variable "extra_instance_profile_permissions" {
  description = "A JSON with a permissions policy document. The policy will be attached to the ASG instance profile."
  type        = string
  default     = null
}

variable "extra_labels" {
  description = "A list of strings to be added as actions runner labels."
  type        = list(string)
  default     = []
}

variable "extra_policies" {
  description = "A map of additional policy ARNs to attach to the instance role."
  type        = map(string)
  default     = {}
}

variable "extra_repos" {
  description = "Additional APT repositories to configure on an instance."
  type = map(
    object(
      {
        source   = string
        key      = string
        machine  = optional(string)
        authFrom = optional(string)
        priority = optional(number)
      }
    )
  )
  default = {}
}

variable "idle_runners_target_count" {
  description = "How many idle runners to aim for in the autoscaling policy."
  type        = number
  default     = 1
}

variable "instance_type" {
  description = "EC2 Instance type"
  type        = string
  default     = "t3a.micro"
}

variable "github_token_secret_arn" {
  description = "ARN of a secret that stores GitHub token. Either github_token_secret_arn or github_app_pem_secret_arn is required."
  type        = string
  default     = null
}

variable "github_app_id" {
  description = "GitHub App that gives out GitHub tokens for Terraform. Required if github_app_pem_secret_arn is not null. For instance, https://github.com/organizations/infrahouse/settings/apps/infrahouse-github-terraform"
  type        = number
  default     = null
}

variable "github_app_pem_secret_arn" {
  description = "ARN of a secret that stores GitHub App PEM key. Either github_token_secret_arn or github_app_pem_secret_arn is required."
  type        = string
  default     = null
}

variable "github_org_name" {
  description = "GitHub organization name."
  type        = string
}

variable "keypair_name" {
  description = "SSH key pair name that will be added to the actions runner instance. By default, create and use a new SSH keypair."
  type        = string
  default     = null
}

variable "max_instance_lifetime_days" {
  description = "The maximum amount of time, in _days_, that an instance can be in service, values must be either equal to 0 or between 7 and 365 days."
  type        = number
  default     = 30

  validation {
    condition     = var.max_instance_lifetime_days == 0 || (var.max_instance_lifetime_days >= 7 && var.max_instance_lifetime_days <= 365)
    error_message = "The max_instance_lifetime_days must be either 0 or between 7 and 365 days."
  }
}

variable "on_demand_base_capacity" {
  description = "If specified, the ASG will request spot instances and this will be the minimal number of on-demand instances. Also, warm pool will be disabled."
  type        = number
  default     = null
}

variable "packages" {
  description = "List of packages to install when the instances bootstraps."
  type        = list(string)
  default     = []
}

variable "post_runcmd" {
  description = "Commands to run after runcmd"
  type        = list(string)
  default     = []
}

variable "puppet_debug_logging" {
  description = "Enable debug logging if true."
  type        = bool
  default     = false
}

variable "puppet_environmentpath" {
  description = "A path for directory environments."
  type        = string
  default     = "{root_directory}/environments"
}

variable "puppet_hiera_config_path" {
  description = "Path to hiera configuration file."
  type        = string
  default     = "{root_directory}/environments/{environment}/hiera.yaml"
}

variable "puppet_manifest" {
  description = "Path to puppet manifest. By default ih-puppet will apply {root_directory}/environments/{environment}/manifests/site.pp."
  type        = string
  default     = null
}

variable "puppet_module_path" {
  description = "Path to common puppet modules."
  type        = string
  default     = "{root_directory}/environments/{environment}/modules:{root_directory}/modules"
}

variable "puppet_root_directory" {
  description = "Path where the puppet code is hosted."
  type        = string
  default     = "/opt/puppet-code"
}

variable "python_version" {
  description = "Python version to run lambda on. Must be one of https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html"
  type        = string
  default     = "python3.12"
}

variable "role_name" {
  description = "IAM role name that will be created and used by EC2 instances"
  type        = string
  default     = "actions-runner"
}

variable "root_volume_size" {
  description = "Root volume size in EC2 instance in Gigabytes"
  type        = number
  default     = 30
}

variable "sns_topic_alarm_arn" {
  description = "ARN of SNS topic for Cloudwatch alarms on base EC2 instance."
  type        = string
  default     = null
}

variable "subnet_ids" {
  description = "List of subnet ids where the actions runner instances will be created."
  type        = list(string)
}

variable "lambda_subnet_ids" {
  description = <<-EOT
    List of subnet IDs where the Lambda functions (runner_registration, runner_deregistration, record_metric) will run.

    REQUIREMENTS: The subnets MUST have either:
    - NAT Gateway/Instance for internet access to AWS services, OR
    - VPC Endpoints for: SSM, Secrets Manager, EC2, AutoScaling, CloudWatch

    The Lambda functions need VPC networking to:
    - Send SSM commands to EC2 instances (start/stop actions-runner service)
    - Access Secrets Manager (GitHub credentials, registration tokens)
    - Call EC2/AutoScaling APIs (describe instances, complete lifecycle actions)
    - Publish CloudWatch metrics

    If not specified, defaults to var.subnet_ids (runner instance subnets).

    WARNING: Lambda functions will fail if subnets lack internet/AWS service access.
  EOT
  type        = list(string)
  default     = null
}

variable "tags" {
  description = "A map of tags to add to resources."
  type        = map(string)
  default     = {}
}
variable "ubuntu_codename" {
  description = "Ubuntu version to use for the actions runner."
  type        = string
  default     = "noble"
}

variable "warm_pool_min_size" {
  description = "How many instances to keep in the warm pool. By default, as many as idle runners count target plus one."
  type        = number
  default     = null
}

variable "warm_pool_max_size" {
  description = "Max allowed number of instances in the warm pool. By default, as many as idle runners count target plus one."
  type        = number
  default     = null
}

variable "compress_userdata" {
  description = <<-EOT
    Compress userdata with gzip to reduce size and work around AWS 16KB limit.

    When enabled, userdata is gzip-compressed before being sent to EC2 instances.
    AWS automatically decompresses it before execution. This can reduce userdata
    size by 60-70%, allowing more packages, files, and configuration.

    Recommended: Enable if userdata_size_info shows approaching limit (>12KB).

    Requirements: gzip command must be available on the system running terraform.
  EOT
  type        = bool
  default     = false
}
