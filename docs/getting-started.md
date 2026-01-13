# Getting Started

This guide walks you through deploying your first GitHub Actions runner using the InfraHouse module.

## Prerequisites

### AWS Resources

Before deploying, you need:

1. **VPC with private subnets** — Runners should be in private subnets with NAT gateway access
2. **Secrets Manager secret** — For GitHub authentication (token or App PEM key)

### GitHub Setup

Choose one authentication method:

=== "GitHub App (Recommended)"

    1. Create a GitHub App in your organization settings
    2. Grant permissions:
        - **Repository**: Actions (read)
        - **Organization**: Self-hosted runners (read/write)
    3. Generate a private key and store it in AWS Secrets Manager
    4. Install the App to your organization

=== "Personal Access Token"

    1. Generate a classic token with `admin:org` scope
    2. Store it in AWS Secrets Manager

## Basic Deployment

### Step 1: Create the GitHub Secret

```hcl
resource "aws_secretsmanager_secret" "github_token" {
  name = "github-actions-runner-token"
}

resource "aws_secretsmanager_secret_version" "github_token" {
  secret_id     = aws_secretsmanager_secret.github_token.id
  secret_string = var.github_token  # Pass via environment variable
}
```

### Step 2: Deploy the Module

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "~> 3.2"

  # Required
  environment              = "production"
  github_org_name          = "your-org"
  subnet_ids               = data.aws_subnets.private.ids
  alarm_emails             = ["oncall@example.com"]
  github_token_secret_arn  = aws_secretsmanager_secret.github_token.arn

  # Sizing
  instance_type            = "t3a.large"
  root_volume_size         = 50
  asg_min_size             = 1
  asg_max_size             = 3
}
```

### Step 3: Apply and Verify

```bash
terraform init
terraform plan
terraform apply
```

After apply completes, verify your runners are registered:

1. Go to your GitHub organization settings
2. Navigate to Actions → Runners
3. You should see runners with labels like `self-hosted`, `Linux`, `aws_region:us-west-2`

## Using Your Runners

In your GitHub Actions workflow:

```yaml
jobs:
  build:
    runs-on: [self-hosted, Linux]
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: make build
```

### Using Custom Labels

If you added `extra_labels = ["docker", "terraform"]`:

```yaml
jobs:
  deploy:
    runs-on: [self-hosted, Linux, terraform]
    steps:
      - name: Deploy infrastructure
        run: terraform apply -auto-approve
```

## Next Steps

- [Configure warm pool](scaling.md#warm-pool) for faster job startup
- [Set up spot instances](scaling.md#spot-instances) to reduce costs
- [Add Puppet configuration](configuration.md#puppet-configuration) for custom software
- [Review monitoring setup](monitoring.md) for compliance requirements
