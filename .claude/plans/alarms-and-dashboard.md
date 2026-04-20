# Plan: Alarms UX fix + docs sidebar + examples page

Tracking: https://github.com/infrahouse/terraform-aws-actions-runner/issues/93
Also closes the docs-site sidebar regression and completes the remaining
docs work from https://github.com/infrahouse/terraform-aws-actions-runner/issues/79.

## Background

This PR bundles two pieces of work:

1. **Docs site regression (already staged locally).** `mkdocs.yml` only had
   `Home: index.md` in `nav`, so the MkDocs Material sidebar rendered with
   no entries even though 8 other pages existed on disk. Also, `docs/examples.md`
   (required by issue #79) was missing.
2. **Alarms UX bug (issue #93).** The CPU CloudWatch alarm in
   `cloudwatch.tf:1` is gated strictly on `var.sns_topic_alarm_arn != null`.
   Callers who set only `alarm_emails` (the documented alert channel) silently
   get no CPU alarm. A compliance auditor flagged EC2s created by this module
   as unmonitored; the module's own contract told the operator they *were*
   monitored.

The two pieces are shipping together because (a) the docs changes are already
made locally on `main` and should land in the next PR, and (b) the fix for #93
requires README + `docs/monitoring.md` updates that naturally belong with a
docs-focused change.

## Confirmed RCA for #93

`cloudwatch.tf:2` — `count = var.sns_topic_alarm_arn != null ? 1 : 0`.

`variables.tf:54-61` — `alarm_emails` is required and validated
(`length > 0`), and is correctly wired into the submodules
(`record_metric`, `runner_deregistration`, `runner_registration`) for Lambda
error monitoring via `terraform-aws-lambda-monitored`.

But the root-module CPU alarm is on a completely different channel
(`sns_topic_alarm_arn`), which is **optional and defaults to `null`**
(`variables.tf:256-260`). Result: the two advertised notification paths are
disjoint. Lambda errors → `alarm_emails`. EC2 CPU → `sns_topic_alarm_arn`.
An operator setting one has no indication the other is silently inactive.

## Strategy

Adopt the pattern already established in `terraform-aws-lambda-monitored`:

- `alarm_emails` is **required** and non-empty (validated). It is the
  load-bearing notification channel.
- The module **always creates its own SNS topic** and subscribes the emails
  to it. No conditional-creation gymnastics, no `count = 0` paths.
- External SNS topics are **permitted but optional** via an
  `alarm_topic_arns` list (default `[]`), for callers who want to fan out
  to PagerDuty/Slack/shared org topics in addition to email.
- All alarms send to the union: `[own_topic] + alarm_topic_arns`.

Then expand coverage and ship a dashboard so operators have one pane of glass
— which is what the "alarms and dashboards story" in #93 actually asks for.

### Breaking change: `sns_topic_alarm_arn` → `alarm_topic_arns`

The current singular `var.sns_topic_alarm_arn` (optional, default `null`) is
replaced by plural `var.alarm_topic_arns` (list, default `[]`). This is the
clean break — no shim, no back-compat alias. Noted in CHANGELOG under
`BREAKING CHANGES`.

`alarm_emails` remains **required** and non-empty (`length > 0` validation
stays). The module guarantees every operator has at least one working
notification path; `alarm_topic_arns` is purely additive fan-out.

Semver: **major bump** (`3.5.0` → `4.0.0`). We maintain strict semver
discipline — a caller-visible variable rename is a breaking change regardless
of how mechanical the migration is.

## Scope

### Part A — docs sidebar + examples page (already done locally, will be in this PR)

- [x] `mkdocs.yml:37-47` — add all 9 pages to `nav`.
- [x] `docs/examples.md` — new page with 6 patterns (token auth, GitHub App,
      spot + on-demand floor, warm pool, labels/large instances, multi-pool).
- [ ] Cross-check variable names used in `docs/examples.md`
      (`warm_pool_min_size`, `warm_pool_max_size`, `on_demand_base_capacity`,
      `extra_labels`, `asg_min_size`, `asg_max_size`) against `variables.tf`
      before commit. Rename or drop any that don't match.

### Part B — alarms UX (#93)

**B1. Always create SNS topic; subscribe required `alarm_emails`; accept optional external topics.**

New `alarms.tf` (keeps `cloudwatch.tf` focused on metric alarms):

```hcl
resource "aws_sns_topic" "alarms" {
  name = "${aws_autoscaling_group.actions-runner.name}-alarms"
  tags = local.default_module_tags
}

resource "aws_sns_topic_subscription" "alarm_emails" {
  for_each  = toset(var.alarm_emails)
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = each.value
}

locals {
  all_alarm_topic_arns = concat(
    [aws_sns_topic.alarms.arn],
    var.alarm_topic_arns,
  )
}
```

Variable changes in `variables.tf`:

- `alarm_emails` — **keep** as-is (already `list(string)`, already validated
  `length > 0`).
- **Remove** `sns_topic_alarm_arn` (singular, optional string).
- **Add** `alarm_topic_arns` — `list(string)`, default `[]`, for external
  fan-out targets (PagerDuty/Slack/shared org topics).

Then in `cloudwatch.tf`:

- `count` gating on `sns_topic_alarm_arn` goes away — alarms are always on.
- `alarm_actions = local.all_alarm_topic_arns` (list, so email + external
  topics both fire).

Naming caveat: the ASG name is computed and so is the topic name — matches
how the record_metric/deregistration Lambdas name their resources.

**B2. Validation — fail fast instead of silent drop.**

`alarm_emails` already has `length > 0` validation. No change needed there —
the bug wasn't validation, it was the CPU alarm ignoring the variable. After
B1, there is no silent-drop path to validate against.

**B3. Expand alarm coverage.**

Additions in `cloudwatch.tf`, all with `alarm_actions = local.all_alarm_topic_arns`.

**ASG count metrics** (`AWS/AutoScaling`, dimension `AutoScalingGroupName`):

- **Total instance count** — alarm on `GroupInServiceInstances` hitting
  `var.asg_max_size` sustained for N minutes. Signals the ASG is pinned at
  max (saturation) — either raise the ceiling or investigate stuck jobs.
- **Zero in-service instances** — alarm on `GroupInServiceInstances == 0`
  when `GroupDesiredCapacity > 0`. Catastrophic; should never sit there.
- **Warm pool depletion** — conditional on warm pool enabled. Alarm on
  `WarmPoolWarmedCapacity == 0` for sustained period when
  `WarmPoolDesiredCapacity > 0`. The whole point of the warm pool is
  latency hiding; if it's empty, the optimization is off.
- **Pending stuck** — `GroupPendingInstances > 0` sustained for **>20 min**.
  Puppet provisioning can legitimately run up to ~15 min on cold boot, so
  the threshold sits above that ceiling to avoid false alarms during normal
  scale-out. Beyond 20 min, something is genuinely stuck (bad LT, capacity
  issues, IAM). CloudWatch doesn't expose a ready-made "launch failure
  count" metric, so this is our proxy.

**Custom `GitHubRunners` metrics** (from `record_metric/lambda/main.py:52`,
namespace `GitHubRunners`, dimension `asg_name`):

- **Registration gap** — metric-math alarm on
  `GroupInServiceInstances - (BusyRunners + IdleRunners) > 0` sustained for
  **>5 min**. Catches the "EC2 is InService but never registered as a
  runner" bug class — exactly the class #93 is about. Timing rationale: the
  ASG only marks an instance InService after the Launch lifecycle hook
  completes (which runs runner_registration Lambda), so by the time an
  instance is InService it should already be a GitHub runner. A 5-min
  sustained gap gives headroom for `record_metric`'s 1-min sampling cadence
  plus GitHub API propagation. Most valuable single new alarm in this plan.
- **BusyRunners saturation** — alarm on `BusyRunners == GroupInServiceInstances`
  sustained for >N minutes with `GroupInServiceInstances == asg_max_size`.
  Means the fleet is 100% busy at max size — jobs are queueing. Distinct
  from the scale-out signal, because scale-out triggers on `IdleRunners`;
  once we're at max capacity, scale-out can't help and operators need to
  know.
- **IdleRunners flatline** — do **not** add. It's redundant with the
  existing `IdleRunnersLow` / `IdleRunnersTooHigh` alarms that drive
  scale-out/scale-in; a third alarm on the same metric creates noise.

**Host-level (CloudWatch agent-dependent)**:

Confirmed: `profile::github_runner` does **not** include
`profile::cloudwatch_agent` (verified in
`puppet-code/modules/profile/manifests/github_runner.pp` and
`profile/manifests/base.pp`). The agent manifest exists in puppet-code but
is only wired into `terraformer`, `openvpn_server`, and `jumphost` roles.
So host-level metrics are **not available by default** on runners.

**Decision: defer host-level alarms entirely to a follow-up PR.**

Tracked in puppet-code: https://github.com/infrahouse/puppet-code/issues/270
(add `profile::cloudwatch_agent` to `role::github_runner`).

This PR ships **no** host-level alarms. When infrahouse/puppet-code#270
lands and the CloudWatch agent is guaranteed on runners, a follow-up PR in
this module will add `disk_used_percent` (path=/) > 85% and
`mem_used_percent` > 85% alarms **unconditionally** — no `enable_*`
variables, no opt-in toggles. Same pattern as every other alarm in this
module: monitored-by-default.

Rationale for dropping the opt-in flags: consistent with the standing
preference to avoid configuration variables for hypothetical needs. An
opt-in flag here would exist solely to bridge a brief window before the
puppet fix lands; not worth the permanent API surface.

Note in `docs/monitoring.md`: mention that host alarms will be added once
the agent is available; callers who can't wait can install the agent via
`post_runcmd` / custom `puppet_manifest` and use their own alarms.

**Already covered, don't duplicate**:

- **Lambda errors** — handled by `terraform-aws-lambda-monitored` in each
  submodule (`record_metric`, `runner_registration`, `runner_deregistration`).
  All three already receive `alarm_emails`. Just document in
  `docs/monitoring.md` so operators know it's covered.

(Resolved: Puppet does not install the CloudWatch agent on
`role::github_runner` today. See "Host-level" section above for opt-in
design and the follow-up path.)

**B4. CloudWatch dashboard.**

One pane of glass — always on, not opt-in. The whole point of the plan is
"this module is monitored by default." If a caller standardizes on Grafana
and doesn't want the AWS dashboard, we revisit with an `enable_dashboard`
variable at that time; we don't pre-gate for a hypothetical.

Single `aws_cloudwatch_dashboard` resource in a new `dashboard.tf`, named
`"${aws_autoscaling_group.actions-runner.name}"` (same deterministic pattern
as the SNS topic). JSON body built via `jsonencode(...)`. Region injected
from `data.aws_region.current.name`.

Rows, top-to-bottom — ordered by how often an operator scans them:

1. **Alarms — what's firing now.** Alarm-status widget listing every
   `aws_cloudwatch_metric_alarm` this module owns (CPU, unhealthy hosts,
   disk/memory if enabled, `IdleRunnersLow`, `IdleRunnersTooHigh`, and the
   Lambda error alarms from the three submodules via
   `terraform-aws-lambda-monitored`). Top row because a green row means you
   scroll past; a red row is why you opened the dashboard.

2. **Runner supply & demand.** Custom metrics already emitted in the
   `GitHubRunners` namespace by `record_metric/lambda/main.py:52`:
   - `BusyRunners` + `IdleRunners` — stacked area, per `asg_name` dimension.
   - Utilization ratio `BusyRunners / (BusyRunners + IdleRunners)` — metric math.
   - Forward-compat slot for `QueuedJobs` if the metric from
     `.claude/plans/future-queued-workflows-metric.md` lands later.

3. **Fleet size & lifecycle state.** `AWS/AutoScaling` namespace, dimension
   `AutoScalingGroupName`:
   - Primary widget: `GroupDesiredCapacity`, `GroupInServiceInstances`,
     `GroupMinSize`, `GroupMaxSize` — desired vs. actual vs. bounds at a glance.
   - Transients widget: `GroupPendingInstances`, `GroupTerminatingInstances`,
     `GroupStandbyInstances`.
   - Warm pool widget (conditional — only rendered when warm pool is enabled,
     so no "no data" holes): `WarmPoolDesiredCapacity`, `WarmPoolWarmedCapacity`,
     `WarmPoolPendingCapacity`, `WarmPoolTerminatingCapacity`.

4. **Autoscaling — why it scaled.** Scaling policies at `autoscaling.tf:35,44`
   are step-scaling triggered by alarms on `IdleRunners` thresholds.
   - `IdleRunners` time series with horizontal annotation lines at
     `var.autoscaling_idle_low` and `var.autoscaling_idle_high` — operator
     sees "we scaled out because we hit the floor at 13:42".
   - Alarm-state timeline for `IdleRunnersLow` and `IdleRunnersTooHigh` —
     shows breach windows.
   - ASG activity isn't a CloudWatch metric; use a markdown widget with a
     deep link to the ASG activity console rather than fabricating a panel.

5. **Host health (EC2, across the ASG).** `AWS/EC2` with
   `AutoScalingGroupName` dimension:
   - `CPUUtilization` average + p95.
   - `StatusCheckFailed` summed.
   - No `CWAgent` widgets in this PR — host metrics aren't available on
     runners until infrahouse/puppet-code#270 lands. The follow-up PR that
     adds disk/memory alarms also adds matching disk/memory widgets here.

6. **Spot interruptions (conditional).** Only rendered when
   `on_demand_percentage_above_base_capacity < 100`:
   - `GroupSpotInstances` time series.
   - Spot interruption events (EventBridge → CloudWatch metric, or link-out
     if we don't already wire this; confirm in implementation pass).

7. **Lambda lifecycle — the runner plumbing.** One compact row, three columns
   (`record_metric`, `runner_registration`, `runner_deregistration`):
   Invocations, Errors, Duration p95, Throttles from `AWS/Lambda`. Error
   alarms are already wired by `terraform-aws-lambda-monitored`; this panel
   is purely for at-a-glance health.

Outputs:
- `dashboard_name` and `dashboard_url` added to `outputs.tf` so callers can
  link from their own runbooks.

Implementation notes:
- Conditional widgets (warm pool, disk/mem, spot) built by assembling the
  widget list with `concat(...)` on known-at-plan conditions — no dynamic
  count on the dashboard resource itself.
- Resist the urge to parameterize thresholds, widths, or refresh intervals.
  Hardcode sensible defaults; revisit only if a real use case appears.

**B5. Documentation.**

- `README.md` — update "What's New" / alarms section and the alarms contract:
  which alarms fire, where they go, what's required vs optional
  (`alarm_emails` required, `alarm_topic_arns` optional fan-out list,
  dashboard always on).
  Let terraform-docs regenerate the variables/outputs tables.
- `docs/monitoring.md` — full alarms inventory (CPU, unhealthy hosts,
  disk/memory if enabled, Lambda errors from each submodule), SNS topic
  provisioning behavior, dashboard layout.
- `docs/examples.md` — add "Fan out to PagerDuty/Slack via `alarm_topic_arns`"
  snippet.
- `CHANGELOG.md` — auto-generated by `git-cliff` on release. Must include
  a `BREAKING CHANGES` entry for `sns_topic_alarm_arn` → `alarm_topic_arns`.

## Migration considerations

Callers currently in one of two states (state-3 from the old plan — only
`sns_topic_alarm_arn` set — is impossible because `alarm_emails` is already
required by validation):

1. **Only `alarm_emails` set** — the broken case #93 reports.
   After this PR: module creates its own SNS topic, subscribes emails, and
   the CPU (plus new) alarms fire against it. Plan diff will show a new SNS
   topic + subscriptions + the CPU alarm flipping from `count=0` to `count=1`
   + new alarms + new dashboard. All additive. Documented as a fix.

2. **Both `alarm_emails` and `sns_topic_alarm_arn` set** — **breaking**.
   `sns_topic_alarm_arn` is removed. Callers must rename to
   `alarm_topic_arns = [<old value>]`. Post-migration: the module creates
   its own topic and subscribes emails there, AND continues to fan out to
   the caller's topic via `alarm_topic_arns`. This is strictly more coverage
   than before, but it's a plan diff callers must approve.

No `moved` blocks required — the new SNS topic, subscriptions, and
dashboard are genuinely new resources. The variable rename is a caller-side
change, not state migration.

Upgrade note for `CHANGELOG.md` and README "Upgrading to 3.6.0":
```hcl
# Before
sns_topic_alarm_arn = "arn:aws:sns:...:shared-alarms"

# After
alarm_topic_arns = ["arn:aws:sns:...:shared-alarms"]
```

## Testing

- `tests/test_module.py` — default selector path (`alarm_emails` only):
  assert the module-owned SNS topic exists, emails are subscribed, and the
  CPU + new alarms fire against it. Assert the dashboard resource exists.
- Test fixture check: confirm `test_data/actions-runner/outputs.tf` exposes
  any new root outputs (`dashboard_url`, `alarm_topic_arn`, etc.) so tests
  can read them. (Per standing feedback on verifying test fixture wrappers.)
- `make test-clean` before PR.

## Out of scope

- Rewriting `record_metric` / `runner_registration` / `runner_deregistration`
  alarm plumbing — already handled by `terraform-aws-lambda-monitored`.
- Full rewrite of `docs/monitoring.md` beyond the alarms contract section.
- Implementing issue #79's remaining items (`examples/`, CODEOWNERS,
  `.pre-commit-config.yaml`, PR/issue templates). Those go in a separate
  "repo hygiene" PR — not coupled to the alarms fix.

## Commit shape

One PR, three conventional-commit commits so the changelog reads cleanly:

1. `docs: fix mkdocs sidebar and add examples page` — `mkdocs.yml` nav +
   `docs/examples.md` + this plan file.
2. `feat!: adopt lambda-monitored SNS pattern; always create alarm topic (#93)` —
   `alarms.tf`, `variables.tf` (drop `sns_topic_alarm_arn`; add
   `alarm_topic_arns`), `cloudwatch.tf`
   (remove `count` gate; use `local.all_alarm_topic_arns`), `outputs.tf`
   updates, README + `docs/monitoring.md` + `docs/examples.md` updates.
   The `!` marks the breaking rename for conventional-commits.
3. `feat: add alarm coverage and CloudWatch dashboard (#93)` — additional
   alarms from B3, `dashboard.tf`, dashboard outputs, docs.

Version bump: `major` (`3.5.0` → `4.0.0`). Breaking rename of
`sns_topic_alarm_arn` → `alarm_topic_arns` is caller-visible; semver
discipline requires a major bump regardless of how mechanical the migration.
Use `make release-major`.
