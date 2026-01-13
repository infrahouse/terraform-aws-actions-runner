# Authentication

The module supports two authentication methods for GitHub API access.

## GitHub App (Recommended)

GitHub Apps provide better security and rate limits than personal access tokens.

### Step 1: Create the GitHub App

1. Go to your organization settings: `https://github.com/organizations/{org}/settings/apps`
2. Click "New GitHub App"
3. Configure:
   - **Name**: `actions-runner-{org}` (must be unique across GitHub)
   - **Homepage URL**: Your org URL
   - **Webhook**: Uncheck "Active" (not needed)

### Step 2: Set Permissions

Under "Permissions":

| Category | Permission | Access |
|----------|------------|--------|
| Repository | Actions | Read |
| Organization | Self-hosted runners | Read & Write |

### Step 3: Generate Private Key

1. Scroll to "Private keys"
2. Click "Generate a private key"
3. Save the downloaded `.pem` file

### Step 4: Install the App

1. Click "Install App" in the left sidebar
2. Select your organization
3. Choose "All repositories" or select specific repos

### Step 5: Store in AWS

```hcl
# Store the PEM key
resource "aws_secretsmanager_secret" "github_app_pem" {
  name = "github-actions-runner-app-pem"
}

resource "aws_secretsmanager_secret_version" "github_app_pem" {
  secret_id     = aws_secretsmanager_secret.github_app_pem.id
  secret_string = file("path/to/private-key.pem")
}
```

### Step 6: Configure the Module

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "~> 3.2"

  # ... other config ...

  github_app_pem_secret_arn = aws_secretsmanager_secret.github_app_pem.arn
  github_app_id             = 123456  # From App settings page
}
```

## Personal Access Token (Classic)

Simpler but with lower rate limits and broader permissions.

### Step 1: Generate Token

1. Go to: `https://github.com/settings/tokens`
2. Click "Generate new token (classic)"
3. Select scope: `admin:org`
4. Generate and copy the token

### Step 2: Store in AWS

```hcl
resource "aws_secretsmanager_secret" "github_token" {
  name = "github-actions-runner-token"
}

resource "aws_secretsmanager_secret_version" "github_token" {
  secret_id     = aws_secretsmanager_secret.github_token.id
  secret_string = var.github_token  # Pass via TF_VAR_github_token
}
```

### Step 3: Configure the Module

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "~> 3.2"

  # ... other config ...

  github_token_secret_arn = aws_secretsmanager_secret.github_token.arn
}
```

## Comparison

| Aspect | GitHub App | Personal Token |
|--------|------------|----------------|
| **Rate Limits** | 5,000/hour per app | 5,000/hour per user |
| **Permissions** | Scoped to specific permissions | Broad `admin:org` |
| **Expiration** | Never (key can be rotated) | Configurable |
| **Audit** | Shows as "app" in logs | Shows as user |
| **Revocation** | Uninstall app | Revoke token |

## Security Best Practices

### Rotate Credentials

**GitHub App:**
```bash
# Generate new key in GitHub App settings
# Update secret in AWS
aws secretsmanager put-secret-value \
  --secret-id github-actions-runner-app-pem \
  --secret-string file://new-private-key.pem
```

**Personal Token:**
```bash
# Generate new token in GitHub settings
# Update secret in AWS
aws secretsmanager put-secret-value \
  --secret-id github-actions-runner-token \
  --secret-string "ghp_newtoken..."
```

### Least Privilege

The module only needs:

- Read runner status (for metrics)
- Create/delete runner registration tokens
- Register/deregister runners

Avoid granting additional permissions beyond what's required.

### Secret Access

The module grants minimal access to secrets:

- Registration Lambda: Read GitHub credentials, Write registration tokens
- Deregistration Lambda: Read GitHub credentials, Delete registration tokens
- EC2 Instances: Read their own registration token only
