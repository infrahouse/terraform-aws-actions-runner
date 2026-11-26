# Authentication

The module's Lambdas call the GitHub API to register, deregister and count runners. They authenticate in one of three
ways; set exactly one of these inputs:

- **`github_app_kms_key_arn` and `github_app_id` (recommended).** The GitHub App's private key is in a KMS key that
  can't export it, and the Lambdas sign the App's JWT with `kms:Sign`. Only principals allowed `kms:Sign` on the key
  can act as the App, and only through AWS.
- **`github_app_pem_secret_arn` and `github_app_id`.** The App's private key is in a Secrets Manager secret, and the
  Lambdas read it and sign with it. Anyone who can read the secret can copy the key and act as the App from anywhere,
  until the key is deleted on GitHub.
- **`github_token_secret_arn`.** A classic token with the `admin:org` scope is in a Secrets Manager secret. Anyone who
  can read the secret can act as the token's user.

As a GitHub App, with either kind of key, each Lambda asks GitHub for an installation token with one permission, the
organization's self-hosted runners: read for record_metric, write for registration and deregistration. A token
expires after an hour.

`github_app_kms_key_arn` can't be combined with another credential input. For backward compatibility,
`github_token_secret_arn` and `github_app_pem_secret_arn` may be set together; the token is then used.

## GitHub App

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

### Step 5: Import the Key into KMS (Recommended)

Import the private key into a KMS key that can't export it, check it, and delete the PEM. The module's README has
the commands: [GitHub App key in KMS](https://github.com/infrahouse/terraform-aws-actions-runner#github-app-key-in-kms).
The test suite runs the same commands on every run of its `kms` case. Don't import the key with Terraform: the key
material would be stored in the Terraform state.

Then configure the module with the key ARN (not an alias ARN or a key ID):

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "6.0.0"

  # ... other config ...

  github_app_kms_key_arn = "arn:aws:kms:us-west-2:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab"
  github_app_id          = 123456 # From App settings page
}
```

The Lambdas' roles get `kms:Sign` on that key, with `kms:SigningAlgorithm = RSASSA_PKCS1_V1_5_SHA_256`, and no
access to any GitHub credential in Secrets Manager. The registration and deregistration Lambdas run in
`lambda_subnet_ids`, so those subnets need a route to KMS: a NAT gateway or a KMS VPC endpoint. The README also
describes key policies that let only the pool's Lambdas sign, rotation and what to do about a lost key.

### Step 5, alternatively: Store the Key in Secrets Manager

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

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "6.0.0"

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
  version = "6.0.0"

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

**GitHub App, key in KMS:** generate a new key on GitHub, import it into a new KMS key, point
`github_app_kms_key_arn` at it and apply, then delete the old key on GitHub. See the README's
[rotation notes](https://github.com/infrahouse/terraform-aws-actions-runner#github-app-key-in-kms).

**GitHub App, key in Secrets Manager:**
```bash
# Generate new key in GitHub App settings
# Update secret in AWS
aws secretsmanager put-secret-value \
  --secret-id github-actions-runner-app-pem \
  --secret-string file://new-private-key.pem
```

A Lambda reads the key once per execution environment and keeps it, so warm Lambdas go on signing with the old key
after the update. Keep the old key on GitHub until every Lambda has started afresh: Lambda replaces execution
environments within hours, and any change to a function's configuration, such as the next `terraform apply` that
updates it, replaces them at once. Then delete the old key on GitHub.

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

Avoid granting additional permissions beyond what's required. As a GitHub App, each Lambda's installation token gets
only the organization's self-hosted runners permission, at the level that Lambda needs.

### Secret Access

The module grants minimal access to credentials:

- Registration Lambda: read the GitHub credential (or `kms:Sign` with the App's key), write registration tokens
- Deregistration Lambda: read the GitHub credential (or `kms:Sign` with the App's key), delete registration tokens
- Record Metric Lambda: read the GitHub credential (or `kms:Sign` with the App's key)
- EC2 Instances: Read their own registration token only
