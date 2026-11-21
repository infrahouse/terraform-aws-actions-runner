# Upgrading

## Upgrading to 6.0.0 (instance role named from the pool)

6.0.0 names the instance role from the pool, the way the Lambda roles are named, and removes the `role_name` input
([#111](https://github.com/infrahouse/terraform-aws-actions-runner/issues/111)).

### What changes

| Role | 5.0.0 name | 6.0.0 name |
|------|------------|------------|
| Instance role | `actions-runner-instance-<role_name>` | `actions-runner-instance-<pool>` |
| Lambda roles | `actions-runner-lambda-<pool>-<function>-<suffix>` | No change |

- `<pool>` is the 6-character random suffix at the end of the ASG name (the `autoscaling_group_name` output), the
  value the Lambda role names have carried since 5.0.0. An existing pool keeps its `<pool>`, so after the upgrade its
  roles read as a set: `actions-runner-instance-lam2UA`, `actions-runner-lambda-lam2UA-reg-…`,
  `actions-runner-lambda-lam2UA-dereg-…` and `actions-runner-lambda-lam2UA-metric-…`.
- The name is unique per pool and stays the same for the life of the pool, so pools in one account no longer need
  different `role_name` values, and a name such as `actions-runner-instance-actions-runner-noble` goes away.
- The instance profile keeps its name and ARN.

### What happens on `terraform apply`

- **The instance role is replaced, and the instance profile's role changes.** Terraform removes the old role from the
  profile and deletes it, then creates `actions-runner-instance-<pool>` and adds it to the profile.
- **The runners roll, as in 5.0.0.** The instance role name is passed to the instances as a Puppet fact, so the launch
  template gets a new version and the ASG starts an instance refresh: new runners launch with the new role, then the
  old ones are terminated.
- From 5.0.0, nothing else is replaced: the Lambda roles, functions and packages don't change.

Runners launched before the upgrade keep credentials of the deleted role until they are replaced. On those runners,
AWS calls fail: jobs that use the instance role during the upgrade can fail, and an old runner can stay in
`Terminating:Wait` for up to the termination hook's 30-minute heartbeat timeout, because it can't complete its own
lifecycle hook. Upgrade when the pool is idle, or be ready to re-run jobs that were running at the time.

### What you need to change

#### Delete `role_name`

Delete `role_name` from your module block. 6.0.0 has no such input, so a module block that still sets it fails at
plan:

```text
Error: Unsupported argument

An argument named "role_name" is not expected here.
```

Nothing replaces it: the module names the role. To refer to the instance role elsewhere, use the `runner_role_name`
and `runner_role_arn` outputs, which now describe `actions-runner-instance-<pool>`.

#### Policies that name the instance role

- A policy that names the instance role by ARN or by name must be updated. A policy that names a role ARN as a
  `Principal` is bound to that role's unique ID, so reference the `runner_role_arn` output and Terraform updates the
  policy after the new role exists.
- `arn:aws:iam::<account>:role/actions-runner-instance-*` still matches every pool's instance role.
- If the identity that runs Terraform may only create roles with certain names, `actions-runner-instance-*` covers the
  new name; a narrower pattern built from your `role_name` doesn't.
- The instance role now carries `<pool>` too, so a pattern built from `<pool>` alone also matches it. In resource
  policies that grant something to the runner's Lambdas by name pattern, match `actions-runner-lambda-*` (or the
  pool's `lambda_role_arn_pattern` output), which no instance role can match.

### Upgrading from 4.x

A pool on 4.x can upgrade straight to 6.0.0, in one `terraform apply`. Everything in
[Upgrading to 5.0.0](#upgrading-to-500-iam-role-name-prefixes) applies: the Lambda roles are replaced, the Lambda
packages are rebuilt, policies that match the old role names must be updated, and `python_version` must be
`python3.12` or `python3.13`. The exception is its `role_name` part: delete `role_name` instead. The instance role
becomes `actions-runner-instance-<pool>`, and the runners roll once.

## Upgrading to 5.0.0 (IAM role name prefixes)

5.0.0 gives the module's IAM roles distinct, predictable name prefixes
([#109](https://github.com/infrahouse/terraform-aws-actions-runner/issues/109)), so a policy can select the
Lambda roles by name pattern without also selecting the runner instances. It also moves the Lambda functions to
terraform-aws-lambda-monitored 2.2.0.

### What changes

| Role | 5.0.0 name |
|------|------------|
| Instance role | `actions-runner-instance-<role_name>`, by default `actions-runner-instance-actions-runner` |
| Registration Lambda role | `actions-runner-lambda-<pool>-reg-<suffix>` |
| Deregistration Lambda role | `actions-runner-lambda-<pool>-dereg-<suffix>` |
| Record-metric Lambda role | `actions-runner-lambda-<pool>-metric-<suffix>` |

- In 4.x the instance role was named `<role_name>` (default `actions-runner`), and each Lambda role was named from
  the first 37 characters of the function name, which starts with the ASG name. The ASG name embeds the launch
  template's timestamped name, so the three Lambda roles of a pool looked alike, started with `actions-runner-` like
  the instance role, and changed whenever the launch template was replaced.
- `<pool>` is the 6-character random suffix at the end of the ASG name (the `autoscaling_group_name` output). It
  stays the same for the life of the pool, including when the launch template is replaced, and differs between pools.
- `<suffix>` is the 26-character suffix Terraform appends to a `name_prefix`.
- No instance role can match `actions-runner-lambda-*`, and no Lambda role can match `actions-runner-instance-*`.

### What happens on `terraform apply`

- **The three Lambda execution roles are replaced.** Each Lambda function is updated in place to use its new role.
  Function names, log groups and alarms don't change.
- **The Lambda packages are rebuilt and re-uploaded.** terraform-aws-lambda-monitored puts its own version into each
  package's hash, so moving from 1.1.1 to 2.2.0 rebuilds the three deployment packages (resolving their dependencies
  again) and uploads them under new S3 keys. The same in-place update that gives each function its new role also
  deploys this new code.
- **The instance role is replaced, and the instance profile's role changes.** The instance profile keeps its name
  and ARN. Terraform removes the old role from the profile and deletes it, then creates the new role and adds it to
  the profile.
- **The runners roll.** The instance role name is passed to the instances as a Puppet fact, so the launch template
  gets a new version and the ASG starts an instance refresh: new runners launch with the new role, then the old ones
  are terminated.

The old instance role is deleted before the new one is in the profile, and runners launched before the upgrade keep
credentials of the deleted role until they are replaced (AWS can take up to an hour to hand a running instance the
credentials of a role swapped into its profile). On those runners, AWS calls fail: jobs that use the instance
role during the upgrade can fail, and an old runner can't complete its own termination lifecycle hook, so it can stay
in `Terminating:Wait` until the hook's heartbeat timeout (30 minutes) before it is terminated. Upgrade when the pool
is idle, or be ready to re-run jobs that were running at the time.

### What you need to change

#### Policies that match the old role names

Any policy that names the old roles, by ARN or by pattern, must be updated: resource-based policies (S3 buckets,
KMS keys, Secrets Manager secrets, ECR repositories, SQS queues), role trust policies, permission boundaries, SCPs,
and conditions on `aws:PrincipalArn`. Point them at the new roles:

- One pool's runner instances: use the `runner_role_arn` output, which now ends in
  `role/actions-runner-instance-<role_name>`.
- Every pool's runner instances, by pattern: `arn:aws:iam::<account>:role/actions-runner-instance-*`.
- One pool's Lambda functions: use the `lambda_role_arn_pattern` output,
  `arn:aws:iam::<account>:role/actions-runner-lambda-<pool>-*`. The 4.x Lambda role names changed with every launch
  template replacement, so a policy could only name them by a pattern that also matched the instance role.
- Every pool's Lambda functions: `arn:aws:iam::<account>:role/actions-runner-lambda-*`.

Also:

- A pattern such as `actions-runner*` still matches both kinds of role. Narrow it to `actions-runner-instance-*` or
  `actions-runner-lambda-*`, whichever you meant.
- A policy that names a role ARN as a `Principal` is bound to that role's unique ID. A role deleted and recreated
  under the same name would not match it either, so update such policies after the upgrade and re-apply them.
  Reference the module's outputs and Terraform does this in the right order.
- If the identity that runs Terraform may only create roles with certain names, allow `actions-runner-instance-*`
  and `actions-runner-lambda-*`.

For example, to let only this pool's Lambda functions read a secret:

```hcl
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "runner_lambdas_only" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:PrincipalArn"
      values   = [module.actions-runner.lambda_role_arn_pattern]
    }
  }
}
```

#### `role_name`

6.0.0 removes `role_name`. If you upgrade from 4.x straight to 6.0.0, skip this part and delete the argument instead
(see [Upgrading to 6.0.0](#upgrading-to-600-instance-role-named-from-the-pool)).

- The module now adds `actions-runner-instance-` in front of `role_name`. Keep the value you had; don't add the
  prefix yourself.
- `role_name` must be 1 to 40 characters from letters, digits and `+=,.@_-`, so that the full name fits IAM's
  64-character limit. Longer values, other characters and `null` now fail at plan. `null` used to give a role named
  after the instance profile; set a string instead.
- Role names are unique per account. If you run several pools in one account, give each its own `role_name`.

#### `python_version`

terraform-aws-lambda-monitored 2.x packages manylinux_2_28 wheels, which only the Amazon Linux 2023 runtimes can load.
`python_version` must be `python3.12` (the default) or `python3.13`. If you set `python3.9`, `python3.10` or
`python3.11`, change it; the functions are updated in place to the new runtime.

### New outputs

| Output | Value |
|--------|-------|
| `runner_role_name` | `actions-runner-instance-<role_name>`; `runner_role_arn` is still the instance role's ARN |
| `lambda_role_name_prefix` | `actions-runner-lambda-<pool>-` |
| `lambda_role_arn_pattern` | `arn:<partition>:iam::<account>:role/actions-runner-lambda-<pool>-*` |
