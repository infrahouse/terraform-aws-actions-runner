# Upgrading

What each release that needs your attention changes, and what to do about it, newest first. The
[Changelog](changelog.md) lists every change from 3.1.0 on; [Earlier releases](#earlier-releases) covers the notes from
before it.

## Upgrading to 6.0.0 (instance role named from the pool)

6.0.0 names the instance role from the pool, the way the Lambda roles are named, and removes the `role_name` input
([#111](https://github.com/infrahouse/terraform-aws-actions-runner/issues/111)). Its instance refresh also waits for
runners that are protected from scale-in instead of skipping them
([#112](https://github.com/infrahouse/terraform-aws-actions-runner/issues/112)).

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
- **The instance refresh waits for protected runners.** The ASG's `instance_refresh` preferences are updated in
  place, `scale_in_protected_instances` from `Ignore` to `Wait`, and the refresh this apply starts already runs with
  `Wait`: see [The instance refresh waits for protected runners](#the-instance-refresh-waits-for-protected-runners).
- From 5.0.0, nothing else is replaced: the Lambda roles, functions and packages don't change.

Runners launched before the upgrade keep credentials of the deleted role until they are replaced. On those runners,
AWS calls fail: jobs that use the instance role during the upgrade can fail, and an old runner can stay in
`Terminating:Wait` for up to the termination hook's 30-minute heartbeat timeout, because it can't complete its own
lifecycle hook. Upgrade when the pool is idle, or be ready to re-run jobs that were running at the time. If a job on
the pool's own runner applies the upgrade, that runner stays protected from scale-in after the job, and the refresh
fails after an hour: see
[When the pool's own runner applies the upgrade](#when-the-pools-own-runner-applies-the-upgrade).

### The instance refresh waits for protected runners

A runner protects itself from scale-in for the length of every job: `gha_prerun.sh` turns the protection on when a
job starts, and `gha_postrun.sh` turns it off when the job ends. Up to 5.0.0 the instance refresh ran with
`ScaleInProtectedInstances` set to `Ignore`, the AWS provider's default. With `Ignore` the refresh leaves a protected
runner out, and replaces it only if the protection is lifted before the refresh ends. When the job that applies an
upgrade runs on the pool's own runner, as it does in a pool that runs its own infrastructure's CD, that runner is
protected from the moment the refresh starts until the job ends. If the refresh ended first (one with nothing else to
replace ends in the second it starts), it ended `Successful` with the runner still on the old launch template version.
Upgrades that were meant to roll the runners, 5.0.0 included, didn't. Before you upgrade a pool that was upgraded to
5.0.0 this way, see [If the pool's own runner applied the upgrade](#if-the-pools-own-runner-applied-the-upgrade).

From 6.0.0 the refresh runs with `ScaleInProtectedInstances` set to `Wait`:

- A runner that is running a job is replaced after the job ends, once `gha_postrun.sh` lifts the protection. The CD
  job that applied the change can finish on it.
- AWS waits up to one hour for the protection to be lifted. If a runner is still protected after that, the refresh
  fails, and the runners it hasn't replaced stay on the old launch template version.
- A runner stays protected for more than an hour when its job runs longer than an hour, and after an upgrade that
  replaces the instance role, as 6.0.0 does: see
  [When the pool's own runner applies the upgrade](#when-the-pools-own-runner-applies-the-upgrade).
- A `terraform apply` that starts a new refresh while one is still waiting cancels the waiting refresh and starts the
  new one.

`Refresh`, which replaces protected runners right away, isn't used: it would terminate runners in the middle of
long-running jobs.

#### When the refresh fails

`terraform apply` starts the refresh and returns, so it doesn't report a refresh that fails later: it ends with
`Apply complete!` and exit code 0. A `terraform plan` after the refresh failed shows `No changes`, because the ASG
already points at the new launch template version. None of the module's CloudWatch alarms watches the refresh, and
the pool keeps working on the old runners. The failure shows only in the refresh itself
(`aws autoscaling describe-instance-refreshes --auto-scaling-group-name <asg>`):

- While the refresh waits, its `Status` is `InProgress`, and its `StatusReason` names the protected runner:

  > Waiting for remaining instances to be available. For example: i-0348eed3561f5338d is protected. Remove instance
  > scale-in protection to continue.

- In the meantime it replaces runners in service that aren't protected, but it doesn't get to the warm pool. In a
  test for #112, where the pool's one runner was protected, it replaced nothing for the whole hour, and no scaling
  activity was recorded.
- About an hour after it started (63 minutes in a test for #112), its `Status` becomes `Failed`:

  > Refresh could not be completed because one or more replacements were not successful. For example:
  > i-0348eed3561f5338d is protected.

The protected runner and the warm pool then stay on the old launch template version. Once no job is running, lift the
protection and start the refresh by hand, as described in
[If the pool's own runner applied the upgrade](#if-the-pools-own-runner-applied-the-upgrade). A later
`terraform apply` that changes the launch template also starts a new refresh.

#### When the pool's own runner applies the upgrade

6.0.0 replaces the instance role. When the apply runs as a job on one of the pool's runners, that runner's
credentials stop working during the apply, `gha_postrun.sh` can't lift the protection (`InvalidClientTokenId`), and
the runner stays protected after its job ends. The refresh waits for it for an hour and then fails, and the runner
stays on the old launch template version. Replace it by hand once no job is running, as described in
[If the pool's own runner applied the upgrade](#if-the-pools-own-runner-applied-the-upgrade): lift the protection,
then start the refresh. The same holds for any later upgrade that replaces the instance role. To avoid it, apply such
an upgrade from a runner outside the pool.

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
becomes `actions-runner-instance-<pool>`, and the runners roll once, with a refresh that waits for protected runners.

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

### If the pool's own runner applied the upgrade

5.0.0's instance refresh leaves out a runner that is protected from scale-in until the refresh ends, and a runner is
protected for the length of every job ([#112](https://github.com/infrahouse/terraform-aws-actions-runner/issues/112),
fixed in 6.0.0). If the upgrade to 5.0.0 was applied by a job on the pool's own runner and the refresh ended before
the job, the refresh skipped that runner and still ended `Successful`, so the runner can still be on the old launch
template version. Its `gha_postrun.sh` ran with the deleted role's credentials and couldn't lift the protection, so
the runner can also still be protected, with no job running on it. The upgrade to 6.0.0 rolls the runners again, but
its refresh waits for a runner that is still protected and fails after an hour, so lift such a protection (step 2
below) before you upgrade.

To check, compare each instance's launch template version with the ASG's. `<asg>` is the module's
`autoscaling_group_name` output:

```bash
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names <asg> \
  --query 'AutoScalingGroups[0].{LaunchTemplateVersion: LaunchTemplate.Version
      || MixedInstancesPolicy.LaunchTemplate.LaunchTemplateSpecification.Version,
    Instances: Instances[].[InstanceId, LaunchTemplate.Version, LifecycleState, ProtectedFromScaleIn]}'
```

An instance whose version is lower than `LaunchTemplateVersion` wasn't replaced. For a pool that sets
`on_demand_base_capacity`, `LaunchTemplateVersion` comes from the ASG's mixed instances policy. Then look at the most
recent refresh:

```bash
aws autoscaling describe-instance-refreshes --auto-scaling-group-name <asg> --max-records 1 \
  --query 'InstanceRefreshes[0].{Status: Status, StatusReason: StatusReason, StartTime: StartTime,
    EndTime: EndTime, ScaleInProtectedInstances: Preferences.ScaleInProtectedInstances}'
```

A refresh that skipped a protected runner shows `Status` `Successful` and `ScaleInProtectedInstances` `Ignore`, and,
when it had nothing else to replace, the same `StartTime` and `EndTime`. A refresh that waited an hour for a
protected runner and gave up shows `Status` `Failed` and `ScaleInProtectedInstances` `Wait`.

To replace such runners by hand:

1. Wait until no job is running on the pool. Each runner has an `instance_id:<instance id>` label, and `busy` is
   `true` while it runs a job:

    ```bash
    gh api --paginate orgs/<org>/actions/runners \
      --jq '.runners[] | {name, status, busy, instance: ([.labels[].name | select(startswith("instance_id:"))][0])}'
    ```

2. A runner that is still protected while it runs no job holds a protection that `gha_postrun.sh` failed to lift.
   Lift it:

    ```bash
    aws autoscaling set-instance-protection --auto-scaling-group-name <asg> \
      --instance-ids <instance id> --no-protected-from-scale-in
    ```

3. Start the refresh with the module's preferences:

    ```bash
    aws autoscaling start-instance-refresh --auto-scaling-group-name <asg> --strategy Rolling \
      --preferences '{"MinHealthyPercentage": 100, "MaxHealthyPercentage": 100, "InstanceWarmup": 0,
        "SkipMatching": false, "AutoRollback": false, "ScaleInProtectedInstances": "Wait",
        "StandbyInstances": "Ignore"}'
    ```

    These are the preferences the module's own refresh runs with from 6.0.0. With `ScaleInProtectedInstances` set to
    `Wait`, a runner that starts a job before the refresh reaches it is replaced after the job instead of being
    skipped. `SkipMatching` set to `false` replaces every runner, including those already on the ASG's version.

4. Follow the refresh with `describe-instance-refreshes` until its `Status` is `Successful`, then check the versions
   again.

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

## Upgrading to 4.0.0 (unified alarm topic)

4.0.0 unifies the alarm notification channel
([#93](https://github.com/infrahouse/terraform-aws-actions-runner/issues/93)). It renames `sns_topic_alarm_arn`
(singular, optional) to `alarm_topic_arns` (list, optional). The module also starts creating its own SNS topic
unconditionally and subscribing `alarm_emails` to it.

### What changes

- The module now **always** creates its own SNS topic for alarms and subscribes every address in `alarm_emails`.
  Previously, EC2 CPU alarms were silently disabled when callers set only `alarm_emails` and not the (optional,
  defaulted-null) `sns_topic_alarm_arn`, resulting in EC2 instances reported as unmonitored.
- **Breaking:** `sns_topic_alarm_arn` (singular, optional) is replaced by `alarm_topic_arns` (list, default `[]`).
  Callers upgrading who previously set `sns_topic_alarm_arn = "<arn>"` should rename to
  `alarm_topic_arns = ["<arn>"]`. After the rename, the module creates its own topic **and** fans out to the provided
  ARNs — strictly more coverage.
- Every alarm in the module (CPU and all future alarms) now fires to the module-owned topic plus any ARNs in
  `alarm_topic_arns`.
- New output `alarm_topic_arn` exposes the module-owned topic for additional subscriptions.

### Who needs to change anything

Only callers who previously set `sns_topic_alarm_arn`. If you only set `alarm_emails`, no caller-side change is
needed — the upgrade is purely additive (you stop being silently unmonitored).

### Rename

```hcl
# Before
module "actions-runner" {
  # ...
  sns_topic_alarm_arn = "arn:aws:sns:us-east-1:123456789012:shared-alarms"
}

# After
module "actions-runner" {
  # ...
  alarm_topic_arns = ["arn:aws:sns:us-east-1:123456789012:shared-alarms"]
}
```

After the rename, alarms fire to **both** the module-owned topic (driven by `alarm_emails`) and every ARN in
`alarm_topic_arns`.

## Upgrading to 3.1.0 (runner_deregistration migration)

The 3.1.0 release migrates the `runner_deregistration` lambda to use `terraform-aws-lambda-monitored` (v1.0.4) for
improved monitoring. **This is a seamless upgrade with no breaking changes and no data loss.**

### What changed

- Internal implementation now uses `terraform-aws-lambda-monitored` module
- Automated dependency packaging for Lambda functions (no more custom package.sh scripts)
- Built-in error monitoring: new SNS alerts for Lambda errors (using existing `alarm_emails` variable)
- Standardized CloudWatch integration with configurable error rate thresholds
- Scheduled sweep interval optimized from 5 minutes to 30 minutes, for improved efficiency
- Existing log groups and data are preserved via `moved` blocks
- **No configuration changes required** - all variables remain the same

### Migration steps

Simply update your module version and apply:

```hcl
module "actions-runner" {
  source  = "registry.infrahouse.com/infrahouse/actions-runner/aws"
  version = "3.1.0" # Update from 3.0.x

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

**Verification:** After upgrade, check CloudWatch Logs - your deregistration logs will still be there with full
history intact.

## Upgrading to 3.0.0 (record_metric migration)

3.0.0 migrates the `record_metric` lambda to the `terraform-aws-lambda-monitored` module (v1.0.0):

- Automated dependency packaging for Lambda functions (no more custom package.sh scripts)
- Built-in error monitoring and alerting via SNS for Lambda functions
- Standardized CloudWatch integration with configurable error rate thresholds
- **Breaking:** Removed `lambda_bucket_name` variable - the module now creates its own S3 bucket for Lambda packages
- **New Required:** `alarm_emails` variable - list of email addresses for Lambda error notifications (required for
  Lambda error monitoring)
- **New Optional:** `error_rate_threshold` variable - error rate percentage threshold for alerting (default: 10.0)

## Earlier releases

Notes on earlier releases, which the [Changelog](changelog.md) doesn't cover:

- **AWS Provider 5 and 6 Support:** The module now supports both AWS provider version 5 and 6, ensuring
  compatibility across major versions.
- **Enhanced Input Validation:** Added validation blocks for `architecture`, `max_instance_lifetime_days`, and other
  critical variables to catch configuration errors early.
- **Improved Type Safety:** All variables now have explicit type declarations for better consistency and error
  prevention.
- **Spot Instances Support:** The module supports spot instances by enabling you to specify the minimum on-demand
  capacity via the `on_demand_base_capacity` variable.
- **Warm Pool Support:** The module now supports a warm pool, allowing you to pre-initialize instances.
- **Bugfixes:**
    - Improved instance lifecycle management.
    - Prevent action runner jobs from being scheduled on instances going back to the warm pool.
    - Orphaned runners are now automatically deregistered.
