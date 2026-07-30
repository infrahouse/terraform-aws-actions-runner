# Launch Inspector-excluded, un-exclude once converged

## Problem

AWS Inspector scans a freshly launched runner roughly 70-80 seconds into boot —
long before the instance has converged. It reads the AMI's package baseline and
files findings against packages that the boot-time upgrade fixes minutes later.

Observed on one production runner:

| Uptime | Wall clock | Event |
|---|---|---|
| 0s | 09:58:07 | boot; AMI baseline package set |
| **73s** | **09:59:20** | **Inspector scans, files a Critical CVE finding** |
| ~213s | 10:01:40 | Puppet first catalog apply begins |
| ~533s | 10:07:00 | boot upgrade completes; package patched |
| 623s | 10:08:31 | first catalog applied (410.40s) |
| 656s | 10:09:04 | cloud-init package install |
| 691s | 10:09:38 | bootstrap lifecycle hook signals CONTINUE |

The instance was verifiably patched to the fixed version, but the finding had
already been filed and stayed open. Inspector re-evaluates SSM inventory every
30 minutes, so findings do eventually auto-close — but with
`max_instance_lifetime_days` able to go as low as 1, runners churn fast enough
that the fleet emits a continuous stream of already-remediated Criticals.

Rebuilding the AMI on a cadence would close the race at the source but generates
a flood of AMI-bump PRs, so it was rejected.

## Approach

Use Inspector's documented exclusion tag as a coordination primitive: launch
instances excluded from scanning, and remove the exclusion only after the
instance has finished patching itself.

Per AWS docs, tagging an instance with key `InspectorEc2Exclusion` (case
insensitive, value optional) makes Inspector mark it excluded and create no
findings for it; coverage status becomes `EXCLUDED_BY_TAG`, and excluded
instances are not billed.

Provisioning order inside the guarded runcmd chain:

1. Regular Puppet provisioning
2. Boot upgrade (UU)
3. Remove `InspectorEc2Exclusion`
4. Signal the bootstrap lifecycle hook with CONTINUE

**Fail-safe by construction.** Steps 1-3 all sit inside the chain that the
cloud-init wrapper guards (see `main.tf:64-67` and issue #86). If the upgrade
fails, or the untag fails, the hook never reaches CONTINUE — the instance is
ABANDONed and terminated. An instance therefore cannot survive while still
carrying the exclusion tag, which removes the fail-open risk of a permanently
invisible host.

## Changes

### 1. Tag instances excluded at launch — `main.tf`

Add to the `aws_autoscaling_group.actions-runner` tag blocks
(alongside the existing ones at `main.tf:224-252`):

```hcl
tag {
  key                 = "InspectorEc2Exclusion"
  propagate_at_launch = true
  value               = "bootstrapping"
}
```

Value is arbitrary; only the key matters to Inspector. Using a descriptive value
makes the intent legible in the console.

### 2. Append the untag to post_runcmd — `main.tf:68`

`var.post_runcmd` is `list(string)` (`variables.tf:196-200`) and is currently
passed straight through. Change to:

```hcl
post_runcmd = concat(
  var.post_runcmd,
  [local.inspector_unexclude_cmd],
)
```

The upgrade step needs no wiring here — it already runs inside Puppet (see
"Where the upgrade comes from" below). This yields exactly the ordering above
without touching `registry.infrahouse.com/infrahouse/cloud-init/aws`: the untag
runs last, immediately before the hook signal.

The command lives in `locals.tf` and is a single `aws ec2 delete-tags` call.
`ec2metadata --instance-id` supplies the instance id — it ships on the Ubuntu
cloud images this module selects and handles IMDSv2, so no token handling is
needed. `awscli` is present by the time it runs: `role::github_runner` includes
`profile::base` → `profile::packages`, and `post_runcmd` runs after Puppet.

### 3. IAM — `data_sources.tf`

Add a statement to `data.aws_iam_policy_document.required_permissions` granting
`ec2:DeleteTags`, scoped two ways: to instances carrying this module's
provenance tag, and to the single tag key, so the role cannot strip arbitrary
tags:

```hcl
statement {
  actions   = ["ec2:DeleteTags"]
  resources = ["arn:aws:ec2:${region}:${account}:instance/*"]
  condition {
    test     = "StringEquals"
    variable = "aws:ResourceTag/created_by_module"
    values   = ["infrahouse/actions-runner/aws"]
  }
  condition {
    test     = "ForAllValues:StringEquals"
    variable = "aws:TagKeys"
    values   = ["InspectorEc2Exclusion"]
  }
}
```

### Explicitly out of scope

Per the docs, exclusion suppresses findings but the Inspector SSM plugin is
still invoked; stopping that would require enabling `instance_metadata_tags` on
the launch template's `metadata_options`. Deliberately not done — this module
did not start that plugin and should not be the thing that stops it. Exclusion
is about findings, and findings are what we are fixing.

## Where the upgrade comes from

Resolved — the upgrade is already owned by InfraHouse's shared Puppet code, and
is guaranteed for every consumer of this module. The chain:

1. `main.tf:32` hardcodes `role = "gha_runner"` (not a variable) into
   `infrahouse/cloud-init/aws`, which sets the `puppet_role` fact.
2. `puppet-code` `hiera.yaml` resolves `"%{::puppet_role}.yaml"`.
3. `data/gha_runner.yaml` (present in both `development` and `production`)
   declares `classes: [role::github_runner]`, and `site.pp` is
   `lookup('classes', {merge => unique}).include`.
4. `profile::github_runner` contains:

```puppet
exec { 'gha-boot-security-upgrade':
  command => 'apt-get update -qq && unattended-upgrade && touch /run/gha-boot-upgrade.done',
  path    => '/usr/bin:/bin:/usr/sbin:/sbin',
  unless  => 'test -f /run/gha-boot-upgrade.done',
  timeout => 1200,
  require => Class['profile::unattended_upgrades'],
}
```

So the ordering the design wants — Puppet, then UU, then untag, then CONTINUE —
already holds: the upgrade runs *inside* the Puppet catalog, well before
`post_runcmd`. On the observed instance the marker was written at 10:07 and the
first catalog completed at 10:08:31, consistent with this.

`profile::unattended_upgrades` also unmasks and enables `unattended-upgrades.service`,
`apt-daily.timer` and `apt-daily-upgrade.timer`, which the base AMI ships masked —
explaining why only `apt-daily.service` appeared in the boot console log.

Two properties worth keeping in mind:

- The marker lives on tmpfs and is written **only on success**, so a failed
  upgrade retries on the next apply rather than being silently skipped.
- Warm-pool runners are hibernated straight after provisioning and a resume is
  not a boot, so this exec is the only thing that patches a pooled instance.
  That also means the untag runs before hibernation — a pooled instance is
  already un-excluded when it later goes into service, so the warm → hot
  transition needs no extra handling.

## Ordering: verified against cloud-init 2.4.0

`main.tf:29` pins `registry.infrahouse.com/infrahouse/cloud-init/aws` at `2.4.0`,
whose `files/ih-bootstrap.sh.tpl` renders:

```bash
%{ for cmd in post_runcmd ~}
${cmd}
%{ endfor ~}

touch /var/run/puppet-done

%{ if lifecycle_hook_name != "" ~}
ih-aws --verbose autoscaling complete "${lifecycle_hook_name}" --result CONTINUE
%{ endif ~}
```

So `post_runcmd` runs **before** the CONTINUE signal, and appending the untag
there gives the intended ordering. The boot log agrees: consumer package
installs ran at 10:09:04 and CONTINUE followed at 10:09:38.

This changed in 2.4.0. Older versions (e.g. 2.2.0) had no bootstrap script and
no `lifecycle_hook_name`; `runcmd` ended with `var.post_runcmd`, so the hook was
signalled by a manual `ih-aws autoscaling complete` placed *inside* `post_runcmd`.
Under that model anything appended after it ran post-CONTINUE.

Known, accepted limitation: `lifecycle_hook_name` makes the wrapper emit
CONTINUE but does not strip commands from a consumer's `post_runcmd`. A consumer
still passing a legacy manual completion signal would push the untag after
CONTINUE. Decided out of scope — consumers are assumed not to do this.

## Open question

### Does a failed upgrade actually reach ABANDON?

The fail-safe rests on a failed `unattended-upgrade` failing the Puppet run,
which fails the runcmd chain, which yields ABANDON. Worth confirming during
testing that a non-zero exit from the exec propagates that far, rather than
Puppet reporting a failed resource while the wrapper still signals CONTINUE.
Not blocking the implementation — it is a test to write, not a design choice.

## Testing

- Assert `InspectorEc2Exclusion` is present on a newly launched instance and
  absent once it reaches InService.
- Assert an instance whose untag step fails is ABANDONed rather than joining.
- Per repo convention, check `test_data/**/outputs.tf` passes through any new
  root outputs before committing.
- Run `make test-clean` before opening the PR.

## References

- <https://docs.aws.amazon.com/inspector/latest/user/scanning-ec2.html>
- <https://repost.aws/knowledge-center/exclude-inspector-scans>
- Issue #86 — bootstrap hook fails closed rather than false-CONTINUE
