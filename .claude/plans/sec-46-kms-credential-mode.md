# SEC-46 slice 2: the KMS credential mode

Linear: [SEC-46](https://linear.app/infrahouse/issue/SEC-46/runner-pools-sign-the-github-app-jwt-in-kms-and-keep-jobs-off-the),
slice 2. Slice 1 is infrahouse-core 1.4.0 (`KMSSigner`, `PEMSecretSigner`, `GitHubAppTokenSource`).

## Goal

A third credential mode, `kms`: the GitHub App's private key lives in a KMS key that can't be exported, and the three
Lambdas sign the App JWT with `kms:Sign`. Every App-authenticated Lambda asks GitHub for a down-scoped installation
token.

## Inputs and rules

- New input `github_app_kms_key_arn` (default `null`). A key ARN only, checked by the variable's own validation:
  the Lambdas' grant is on the key ARN, and a grant on an alias doesn't cover the key.
- Credential rules, checked at plan by preconditions on `terraform_data.github_credentials`
  (`github_credentials.tf`). A variable validation can't read another variable before Terraform 1.9, and the module
  supports `~> 1.5`.
  - no credential input: error;
  - the KMS key with a token or a PEM: error;
  - the KMS key, or a PEM without a token, and no `github_app_id`: error;
  - a token with a PEM: accepted, the token wins, as before.
- `local.github_credentials` derives `{type, arn}` once; `registration.tf` and `cloudwatch.tf` pass it to the three
  sub-modules. `github_credentials.secret` becomes `github_credentials.arn` inside the sub-modules.

## IAM and Lambda environment per mode

| Mode    | Credential grant                                                                 | Environment                       |
|---------|----------------------------------------------------------------------------------|-----------------------------------|
| `token` | `secretsmanager:GetSecretValue` on the secret (unchanged)                       | `GITHUB_SECRET` (unchanged)       |
| `pem`   | `secretsmanager:GetSecretValue` on the secret (unchanged)                       | `GITHUB_SECRET` (unchanged)       |
| `kms`   | `kms:Sign` on the key ARN, `kms:SigningAlgorithm = RSASSA_PKCS1_V1_5_SHA_256`   | `GITHUB_APP_KMS_KEY_ARN`, no secret |

## Lambdas

- `infrahouse-core ~= 1.4`.
- The token source and signer are built at module scope from the module-scope `boto3.Session`.
- Permissions: registration and deregistration `organization_self_hosted_runners: write`, record_metric `read`
  (GitHub's "Self-hosted runners" organization permission: list and get runners are read; the registration token and
  deleting a runner are write).
- record_metric logs how long the token took, so its `timeout(5)` budget can be measured.

## Import procedure

One procedure: the README's "GitHub App key in KMS" section holds the import and check commands between markers, and
the test fixture runs those blocks. OpenSSL 3 is required for AES key wrap with padding.

## Tests

- Validation (no AWS): each credential rule, the alias ARN, the valid combinations.
- Lambda unit tests (no AWS): each mode builds the right source and signer once and asks for the right permissions.
- `kms-noble-aws-6`: a fresh KMS key per run, imported with the README's commands and scheduled for deletion at
  teardown; runners register, the sweep succeeds, record_metric publishes; IAM simulation of each Lambda role.
