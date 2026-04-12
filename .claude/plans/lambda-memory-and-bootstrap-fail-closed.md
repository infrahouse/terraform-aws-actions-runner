# Plan: Lambda Memory Monitoring + Fail-Closed Bootstrap

Two bugs motivated this work. They're independent tracks but will ship together.

## Motivation

### Bug 1 — Lambda OOM with no alerting

`modules/runner_deregistration/lambda/main.py` runs on a schedule and sweeps GitHub
Actions runners whose EC2 instances no longer exist. It crashed on out-of-memory
because of [infrahouse-core#136](https://github.com/infrahouse/infrahouse-core/issues/136),
and the failure was silent — no memory alarm, no notification. Too many stale runners
piled up, each one made the next invocation worse, and the module that was supposed
to clean up was the one that couldn't.

Upstream `terraform-aws-lambda-monitored` now supports memory alarms
([issue 22](https://github.com/infrahouse/terraform-aws-lambda-monitored/issues/22),
released in 1.1.0). This repo should adopt it and also add a regression test that
catches any future lambda from creeping toward its memory limit before the alarm
fires in prod.

### Bug 2 — Bootstrap lifecycle hook signals CONTINUE on failed cloud-init

[#86](https://github.com/infrahouse/terraform-aws-actions-runner/issues/86) and
root cause [terraform-aws-cloud-init#84](https://github.com/infrahouse/terraform-aws-cloud-init/issues/84):
cloud-init's `runcmd` module does not stop on failure, so the trailing
`ih-aws ... complete bootstrap` call executed even when puppet or consumer
`post_runcmd` steps had failed. Broken instances joined the fleet with CONTINUE.

`terraform-aws-cloud-init` 2.3.0 now accepts a `lifecycle_hook_name` input that
installs the completion signal inside a `set -euo pipefail` wrapper with an `ERR`
trap. On any failure the wrapper signals `ABANDON` and the ASG terminates the
instance.

## Track 1 — Memory alarms on the three lambdas

### Changes

1. **Bump `lambda-monitored` 1.0.4 → 1.1.0** in:
   - `modules/runner_registration/main.tf`
   - `modules/runner_deregistration/main.tf`
   - `modules/record_metric/main.tf`

2. **Hardcode memory and alarm threshold** (no new variables):
   - `memory_size = 256` (currently 128 — 128 was the trigger of the incident)
   - `memory_utilization_threshold_percent = 80`

   Per user preference: if we know what the right value is, hardcode it. If 256
   turns out wrong we change the constant. Lambda Insights is enabled as a
   side-effect of setting the threshold; the cost (~$0.30/month/function) is
   worth it — "availability is more important than cost; want cheap, use GitHub
   runners."

3. **No root-level variables** for memory size or threshold. Callers don't tune
   these.

### Decided

Keep Lambda Insights enabled (so the prod alarm works) and in the test poll
`LambdaInsights/memory_utilization` via `cloudwatch:GetMetricStatistics`, same
approach as `terraform-aws-lambda-monitored`'s own test. Not log parsing.

## Track 2 — Regression test for lambda memory usage

### What the test run already triggers naturally

- **`record_metric`** — runs every 1 minute on schedule (`modules/record_metric/eventbridge.tf`).
  During a 10-15 minute test run, it fires many times. No explicit invoke needed.
- **`runner_registration`** — fires on EC2 instance launch lifecycle hooks
  (`modules/runner_registration/eventbridge.tf`). The ASG brings up at least
  `min_size` instances during the test, plus any instance refresh. No explicit
  invoke needed.
- **`runner_deregistration`** — two separate triggers:
  - **Terminate lifecycle hook**: fires on instance refresh. But this path goes
    through `_handle_deregistration_hook`, which is lightweight (one SSM
    command to stop the service). **Not** the path that OOM'd.
  - **30-minute schedule**: goes through `_clean_runners`, the path that OOM'd.
    30 minutes is longer than a typical test run, so this lambda **does**
    need an explicit invocation from the test to get coverage on the real
    failure mode.

### Shape

1. Fold into the existing `test_module` in `tests/test_module.py`, inside the
   `with terraform_apply(...)` block and after `ensure_runners()` returns
   successfully. Same ASG, same lambdas, no extra apply cycle.

2. Surface each sub-module's lambda function name as a root-module output so
   the test can look it up without scraping by prefix. Add
   `lambda_function_name` outputs to `modules/runner_registration`,
   `modules/runner_deregistration`, `modules/record_metric`, then root-level
   outputs `registration_lambda_name`, `deregistration_lambda_name`,
   `record_metric_lambda_name`.

3. **Explicitly invoke** `runner_deregistration` a few times (e.g. 3) with a
   minimal sweep event — a payload lacking `detail.LifecycleHookName` so the
   lambda falls into the `_clean_runners` branch. The other two lambdas are
   already firing naturally; no explicit invoke for them.

4. For each of the three function names, poll
   `cloudwatch:GetMetricStatistics` with
   `Namespace=LambdaInsights, MetricName=memory_utilization,
   Dimensions=[{function_name: ...}]` over the last 15 minutes. Lambda
   Insights can lag ~15 min before a datapoint becomes queryable, so wrap
   the poll in a `timeout(900)` loop, same pattern as lambda-monitored's
   own test.

5. Assert `max(Maximum) < 70` per function. If any exceeds, the test fails
   and the commit that bumped memory pressure is identified.

### Why 70% and not something else

Alarm fires at 80%. Test uses 70% as a tighter regression gate so changes are
caught in CI *before* they'd page in prod. 10-point margin is enough to
absorb normal variance (object graph size, GC timing) while still flagging
real regressions.

## Track 3 — Fail-closed bootstrap lifecycle hook

### Changes

1. **Bump `cloud-init` 2.2.3 → 2.3.0** in `main.tf` (`module "userdata"`).
2. Pass `lifecycle_hook_name = local.bootstrap_hookname` to the userdata module.
3. Remove the trailing `"ih-aws --verbose autoscaling complete ${local.bootstrap_hookname}"`
   from `post_runcmd`. Consumers' `var.post_runcmd` passes through unchanged.

### Validation

Existing `ensure_runners()` in `tests/conftest.py` already asserts runners come
online. If the new wrapper incorrectly sends ABANDON the ASG never stabilizes
and the test fails with a timeout. No new test case needed — the happy path is
covered. An unhappy-path test (seed a failing `post_runcmd` and assert the
instance is terminated) would be nice-to-have but is probably overkill for this
PR.

### Status

**I already made the main.tf edits for this track** before the user said to
pause for plan discussion. The change is still in the working tree but not
committed. I can revert it on request, or leave it in place if the plan is
approved as-is.

## Rollout order

If the plan is approved:

1. Track 3 (smallest — 1 file, 3-line net change). Run full test to validate
   bootstrap still works end-to-end.
2. Track 1 (terraform-only changes across three sub-modules).
3. Track 2 (Python test code).

Combined into a single PR since Tracks 1 and 2 are tightly coupled (the test
validates Track 1's memory sizing).

## Status

Plan approved. Implementing in the order listed under "Rollout order".
