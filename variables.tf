variable "ami_id" {
  description = "AMI id for EC2 instances. By default, latest Ubuntu var.ubuntu_codename."
  type        = string
  default     = null
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
        source = string
        key    = string
      }
    )
  )
  default = {}
}

variable "instance_type" {
  description = "EC2 Instance type"
  type        = string
  default     = "t3a.micro"
}

variable "github_token_secret_arn" {
  description = "ARN of a secret that stores GitHub token."
  type        = string
}

variable "github_org_name" {
  description = "GitHub organization name."
  type        = string
}

variable "keypair_name" {
  description = "SSH key pair name that will be added to the postfix instance.By default, create and use a new SSH keypair."
  type        = string
  default     = null
}

variable "environment" {
  description = "Environment name. Passed on as a puppet fact."
  type        = string
}

variable "max_instance_lifetime_days" {
  description = "The maximum amount of time, in _days_, that an instance can be in service, values must be either equal to 0 or between 7 and 365 days."
  type        = number
  default     = 30
}

variable "packages" {
  description = "List of packages to install when the instances bootstraps."
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

variable "subnet_ids" {
  description = "List of subnet ids where the actions runner instances will be created."
  type        = list(string)
}

variable "ubuntu_codename" {
  description = "Ubuntu version to use for the actions runner."
  type        = string
  default     = "jammy"
}
