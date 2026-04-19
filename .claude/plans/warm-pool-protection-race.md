# Plan: Fix `SetInstanceProtection` scale-in race

Tracking: https://github.com/infrahouse/terraform-aws-actions-runner/issues/81
Customer tracking: INF-1292

> Filename is historical. Earlier framing ("warm-pool resume race") was
> invalidated by CloudTrail analysis — the actual race is at scale-in, not
> warm-pool resume. Rename on the PR that closes this plan out.

## Confirmed RCA

The prerun hook's `SetInstanceProtection(protect=true)` fails because the
instance is in `Terminating:Wait` at the moment the hook fires.

CloudTrail evidence (`i-0f3066deda420ab46`, 2026-01-30 10:31:07Z, account
611021602836, us-west-1):

1. 10:31:04 — ASG scale-in → instance enters `Terminating:Wait`, deregistration
   Lambda fires.
2. 10:31:15 — Lambda issues SSM `systemctl stop actions-runner` (11s after
   state transition).
3. 10:31:07 — inside that 11s gap, GitHub dispatches a queued job to the
   runner's open long-poll. `gha_prerun.sh` fires, calls
   `SetInstanceProtection(true)` on a `Terminating:Wait` instance, AWS
   rejects it, job exits 1.
4. 10:31:09 — postrun runs `SetInstanceProtection(false)` and succeeds;
   AWS permits clearing protection on terminating instances.
5. 10:31:18 — ASG `TerminateInstances`.

The "instance ID doesn't exist" framing in INF-1292 was a timing artifact:
the instance was terminated (at 10:31:18) and aged out of
`describe-instances` retention (~1h for terminated records) by the time
the investigator looked.

## Strategy

We cannot close the race cleanly — GitHub's actions-runner is closed to us
and `--ephemeral` mode conflicts with our persistent-runner product
requirement (customer distributed-compilation use case). We also can't
shrink the SSM round-trip low enough to make the window vanish.

So the plan accepts that a job *can* land on a `Terminating:Wait` instance,
and makes that path successful instead of failing: prerun tolerates the
state, systemd's graceful-stop lets the in-flight job finish (including
postrun), and `ExecStopPost` closes out the lifecycle hook. A heartbeater
keeps the hook alive for long jobs.

In addition, we fix three real bugs in the current systemd unit that are
biting silently today (they cause SIGKILL of in-flight jobs during any
clean runner stop, including manual operator actions — not just scale-in).

## Research notes that shape the design

From reading the actions-runner source + GitHub docs:

- **SIGTERM is graceful.** `Runner.cs` → `HostContext.ShutdownRunner()` →
  `jobDispatcher.ShutdownAsync()` waits for the in-flight Worker to
  finish, then exits. `svc.sh stop` sends SIGTERM with `TimeoutStopSec=5min`.
- **`runsvc.sh` wraps the runner** with a `trap 'kill -INT $PID' TERM INT`.
  Our current setup uses `run.sh` directly — no trap. Switch to
  `runsvc.sh` (shipped in the actions-runner tarball).
- **GitHub recommends `--ephemeral` for autoscaling**, which we can't use.
  That means we have to handle graceful stop ourselves.
- **No runner-exposed filesystem state** (`.job` files, PID files) suitable
  for flock() coordination. The runner is opaque to external coordination.
- **ASG lifecycle hook limits:** `heartbeat_timeout` is 30s–7200s (2h max
  per call); `GlobalTimeout` is 48h (hard ceiling across all heartbeats);
  `default_result = ABANDON` on a termination hook does **not** save the
  instance — it still terminates, just marked abnormal in
  CloudTrail/EventBridge.

---

# Part 1 — `terraform-aws-actions-runner`

## Changes in this repo

### 1.1 Deregistration lifecycle hook — set `heartbeat_timeout` to 30min

Currently, the hook uses the AWS default (3600s / 1h). Set it to 1800s
(30min):

```hcl
# in modules/runner_deregistration/eventbridge.tf (or wherever the hook is)
heartbeat_timeout = 1800  # 30 minutes; heartbeater extends it every 10 min
default_result    = "ABANDON"  # observability; outcome identical to CONTINUE
```

Reasoning: the heartbeater (Part 2, §2.4) fires every 10min and resets
this clock. A 30min timeout gives 3 heartbeats of slack before expiry —
enough to tolerate a single missed tick (transient API error, heartbeater
restart) without losing the job. Shorter also means a genuinely broken
heartbeater is caught fast (instance terminates in ≤30min rather than
lingering for hours). We don't need the AWS max of 2h because the
heartbeater handles arbitrarily long jobs; the timeout is really just
a watchdog on the heartbeater itself.

The `ABANDON` vs `CONTINUE` choice is purely signaling — for termination
hooks AWS terminates either way, but `ABANDON` produces a distinguishable
event stream we can debug against.

### 1.2 Simplify `runner_deregistration` Lambda

Current Lambda: sends SSM `systemctl stop` and waits for completion, then
calls `CompleteLifecycleAction`. With the new design, the Lambda's job
shrinks dramatically.

New shape of `modules/runner_deregistration/lambda/main.py`:

- On `Warmed:Terminating:Wait`: call `CompleteLifecycleAction(CONTINUE)`
  immediately. No SSM needed — no runner service ever ran on a warm-pool
  instance. Same fast path as today.
- On regular `Terminating:Wait`: send SSM
  `systemctl stop actions-runner.service`. Do **not** wait for the
  command to complete. Do **not** call `CompleteLifecycleAction` — the
  instance's `ExecStopPost` will handle it once the runner exits.
- Remove the `execute_command` wait-loop (it's what made the Lambda
  slow and what contributed to the 11s race window).
- Keep existing error handling. If SSM send itself fails, log and let
  the hook time out; `default_result = ABANDON` catches it.

Net effect: Lambda invocation goes from "wait for SSM + complete" (~10s+)
to "fire SSM + return" (~1s). Doesn't change the race — prerun tolerance
does — but reduces Lambda noise.

### 1.3 Move registration-token cleanup out of the deregistration Lambda

Currently, the deregistration Lambda deletes
`GH-reg-token-FJVASl-<instance_id>`. Delete this responsibility from the
Lambda entirely. Puppet is what actually consumes the token
(`exec { 'register_runner': ... }` in `profile::github_runner::register`,
puppet-code); the cleanest lifetime is "whoever uses it, deletes it".
Puppet will delete the secret right after a successful
`register_runner` exec (see Part 2 §2.8 for the puppet-code side).

Rationale: co-locates creation-use-deletion with the one actor that
knows the token was spent on GitHub's side. If Puppet retries (e.g., a
later agent run on the same instance), `register_runner`'s
`creates => ".credentials"` guard skips re-registration, and the
cleanup exec on the secret is idempotent. No Lambda-side state needed.

### 1.4 Instance profile IAM additions

New permissions scoped to this ASG's hook ARN / secret ARN:

- `autoscaling:RecordLifecycleActionHeartbeat` — for the heartbeater
  running on the instance.
- `autoscaling:CompleteLifecycleAction` — for `ExecStopPost` on the
  actions-runner systemd unit.
- `autoscaling:DescribeAutoScalingInstances` — already used by
  `start-actions-runner.sh`; confirm present, add if not.
- `secretsmanager:DeleteSecret` — for Puppet's cleanup exec (§1.3 / Part
  2 §2.8). Scope narrowly to `${registration_token_secret_prefix}-${aws:PrincipalTag/instance_id}`
  or equivalent condition so one instance can only delete its own token
  secret.

Add to `data_sources.tf` → `aws_iam_policy_document.runner_policy`.

### 1.5 Pass the hook name to Puppet as a custom fact

The new scripts in Part 2 (`gha-on-runner-exit.sh`, heartbeater) need
the deregistration lifecycle hook's name. That name is defined in
Terraform (`local.deregistration_hookname`) and isn't discoverable at
runtime from the instance's own metadata.

Inject it as a custom Puppet fact alongside the existing
`registration_token_secret_prefix` fact. Mechanism is the same — the
`cloud-init` module's external-facts YAML written to
`/etc/puppetlabs/facter/facts.d/`. New fact:

- `deregistration_hookname` — value from `local.deregistration_hookname`.

Puppet consumes it as `$facts['deregistration_hookname']` and injects
it into the systemd unit and the heartbeater `.service` as an
`Environment=DEREGISTRATION_HOOK_NAME=<value>` directive (see Part 2
§2.1, §2.4). The scripts read it from the env and no-op if empty, which
is how new-Puppet + old-Terraform stays safe (see backwards-compat in
Open questions).

(ASG name isn't passed — the scripts resolve it at runtime via
`ih-ec2 tags | jq -r '."aws:autoscaling:groupName"'`, or from
`aws autoscaling describe-auto-scaling-instances`.)

### 1.6 Test fixtures

- Bump pins in `test_data/actions-runner/` to a puppet-code and
  infrahouse-toolkit version that includes the Part-2 changes.
- New integration test: trigger scale-in with `idle_runners_target_count`
  below current idle count, simultaneously dispatch a job through
  GitHub. Assert job completes successfully, instance terminates cleanly,
  no `ValidationError` in CloudTrail. Non-trivial to run reliably in CI;
  may be a manual smoke test documented in the README rather than
  automated.

### 1.7 Docs

- `docs/troubleshooting.md` — new section "Jobs failing with
  `SetInstanceProtection` error". Describe the historical symptom, point
  at the fixes, and tell the reader how to confirm (check the prerun
  exit code, the lifecycle state in CloudTrail, etc.).
- Close GH #81 and Linear INF-1292 once Part 1 + Part 2 land in prod.

## Release & rollout (Part 1)

- Minor version bump (user-visible config surface changes: hook timeout,
  new IAM permissions).
- CHANGELOG: document the IAM additions explicitly — existing deployments
  need to re-apply to pick up the new statements on the instance profile.
- Must be released after Part 2 is available as a pinned puppet-code
  version; otherwise the module ships without the corresponding
  `ExecStopPost` script and things stall in `Terminating:Wait`.

---

# Part 2 — `puppet-code`

## Changes in this repo

**All development happens in `environments/development/modules/profile/github_runner/`
only.** Do not touch `environments/sandbox/` or the top-level `modules/`
(global) during initial implementation.

Promotion sequence (after Parts 3 → 2 → 1 are complete and working in
development):

1. `environments/development/` — initial implementation + iteration.
2. `environments/sandbox/` — copy over once dev is stable.
3. `modules/` (global) — copy over once sandbox is stable.

This mirrors how other `github_runner` changes have been promoted in
recent history (e.g. PRs #126, #174) and keeps the blast radius of
in-progress work bounded.

### 2.1 Fix the systemd unit

Edit `modules/profile/templates/github_runner/actions-runner.service.erb`
to match GitHub's recommended configuration, plus our lifecycle hook:

```
[Unit]
Description=GitHub self-hosted runner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=<%= @start_script %>
ExecStopPost=/usr/local/bin/gha-on-runner-exit.sh
Environment=DEREGISTRATION_HOOK_NAME=<%= @deregistration_hookname %>
WorkingDirectory=<%= @runner_package_directory %>
User=<%= @github_runner_user %>
Group=<%= @github_runner_group %>
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=21600
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`@deregistration_hookname` is read from `$facts['deregistration_hookname']`
in `service.pp` (empty string fallback if the fact is missing, so
`ExecStopPost` no-ops cleanly on old-Terraform ASGs).

Three real fixes + one new hook:
- `KillMode=process` (was default `control-group`, which would SIGKILL the
  runner's Worker process and kill jobs mid-flight).
- `TimeoutStopSec=21600` (6h, covers any realistic Terraform apply; was
  default 90s, too short).
- `KillSignal=SIGTERM` — explicit, matches GitHub's template.
- `ExecStopPost=/usr/local/bin/gha-on-runner-exit.sh` — new, see 2.3.

### 2.2 Make `start-actions-runner.sh.erb` signal-transparent

Replace the final `exec ./run.sh` with `exec ./runsvc.sh` — the runner
tarball ships both, and `runsvc.sh` does the right thing with signals
(`trap 'kill -INT $PID' TERM INT`). Final shape of the wrapper:

```bash
#!/usr/bin/env bash
set -eu

instance_id=$(ec2metadata --instance-id)

while true; do
  state=$(aws autoscaling describe-auto-scaling-instances \
          --instance-ids "$instance_id" \
          --query 'AutoScalingInstances[0].LifecycleState' --output text)
  [[ "$state" == "InService" ]] && break
  echo "The instance in state $state. Waiting."
  sleep 5
done

exec <%= @runner_package_directory %>/runsvc.sh
```

The `exec` replaces the bash PID with runsvc.sh, so when systemd sends
SIGTERM to the unit's main PID it goes to runsvc.sh (which traps
TERM→INT and forwards to Runner.Listener). Bash's own signal handling
during the pre-InService wait loop is fine: SIGTERM during `sleep` kills
the script, and there's no runner to stop yet at that point.

### 2.3 New script — `gha-on-runner-exit.sh`

File: `modules/profile/files/github_runner/gha-on-runner-exit.sh`
(managed by `service.pp`).

```bash
#!/usr/bin/env bash
# Called by systemd's ExecStopPost when actions-runner.service stops.
# If the ASG wants this instance terminated, complete the deregistration
# lifecycle hook now so the instance can go away cleanly.
set -eu

hook_name="${DEREGISTRATION_HOOK_NAME:-}"
[[ -z "$hook_name" ]] && exit 0  # old-Terraform ASG — old Lambda handles it

instance_id=$(ec2metadata --instance-id)
state=$(aws autoscaling describe-auto-scaling-instances \
        --instance-ids "$instance_id" \
        --query 'AutoScalingInstances[0].LifecycleState' --output text 2>/dev/null || echo "")

case "$state" in
  Terminating:Wait|Terminating:Proceed)
    ih-aws autoscaling complete --hook "$hook_name" --result CONTINUE
    ;;
esac
```

`DEREGISTRATION_HOOK_NAME` is set in the systemd unit via an
`Environment=` line populated from the `deregistration_hookname` Puppet
fact (see §1.5). Empty → old-Terraform ASG → script exits cleanly and
leaves the old Lambda in charge. Uses the existing
`ih-aws autoscaling complete` (already in infrahouse-toolkit).

### 2.4 New systemd heartbeater

Two new files:

**`modules/profile/files/github_runner/gha-lifecycle-heartbeater.sh`:**
```bash
#!/usr/bin/env bash
# No-op unless the instance is in Terminating:Wait. Fire-and-forget.
set -eu

hook_name="${DEREGISTRATION_HOOK_NAME:-}"
[[ -z "$hook_name" ]] && exit 0

instance_id=$(ec2metadata --instance-id)
state=$(aws autoscaling describe-auto-scaling-instances \
        --instance-ids "$instance_id" \
        --query 'AutoScalingInstances[0].LifecycleState' --output text 2>/dev/null || echo "")

if [[ "$state" == "Terminating:Wait" ]]; then
  asg=$(ih-ec2 tags | jq -r '."aws:autoscaling:groupName"')
  aws autoscaling record-lifecycle-action-heartbeat \
    --auto-scaling-group-name "$asg" \
    --lifecycle-hook-name "$hook_name" \
    --instance-id "$instance_id"
fi
```

**`modules/profile/files/github_runner/gha-lifecycle-heartbeater.{service,timer}`:**
Systemd timer + oneshot service pair. Timer triggers every 10min. The
service has `Restart=on-failure` and
`Environment=DEREGISTRATION_HOOK_NAME=<%= @deregistration_hookname %>`
populated from the Puppet fact (§1.5). No metric, no alarm.

### 2.5 Update `gha_prerun.sh`

`modules/profile/files/github_runner/gha_prerun.sh`:

```bash
#!/usr/bin/env bash
set -eu

sudo chown -R "$USER" "$GITHUB_WORKSPACE"

# Try to protect this instance from scale-in. If the ASG has already
# decided to terminate us, protection is meaningless; let the job run
# and let the deprovisioning path finish us off cleanly.
if ! /usr/local/bin/ih-aws autoscaling scale-in enable-protection 2>/tmp/prerun_err; then
  instance_id=$(ec2metadata --instance-id)
  state=$(aws autoscaling describe-auto-scaling-instances \
          --instance-ids "$instance_id" \
          --query 'AutoScalingInstances[0].LifecycleState' --output text 2>/dev/null || echo "")
  case "$state" in
    Terminating:Wait|Terminating:Proceed)
      echo "prerun: instance is in $state — skipping protect, job will proceed" >&2
      ;;
    *)
      cat /tmp/prerun_err >&2
      exit 1
      ;;
  esac
fi
```

Keeps the existing behavior for real errors; only the specific
"we're already terminating" path gets tolerated.

### 2.6 `gha_postrun.sh` — unchanged

Keep the existing `disable-protection || true` behavior. `ExecStopPost`
and the heartbeater handle the new responsibilities.

### 2.7 Delete the registration token after register

In `modules/profile/manifests/github_runner/register.pp`, leave
`register_runner` alone and add a separate `delete_registration_token`
exec that fires via `notify`/`refreshonly`. No error swallowing — if
the delete genuinely fails, Puppet reports it and the operator
investigates.

```puppet
exec { 'register_runner':
  user    => $user,
  path    => "/usr/bin:/usr/local/bin:${runner_package_directory}",
  cwd     => $runner_package_directory,
  command => "ih-github runner --registration-token-secret ${token_secret} --org ${org} register \
--actions-runner-code-path ${runner_package_directory} ${url} ${labels_arg}",
  creates => "${runner_package_directory}/.credentials",
  require => [ Exec[extract_runner_package] ],
  notify  => Exec['delete_registration_token'],
}

exec { 'delete_registration_token':
  user        => $user,
  path        => "/usr/bin:/usr/local/bin",
  command     => "aws secretsmanager delete-secret --secret-id ${token_secret} --force-delete-without-recovery",
  refreshonly => true,
}
```

Flow:
- First Puppet run on a fresh instance: `register_runner` runs → notify
  fires → `delete_registration_token` runs → secret deleted.
- Subsequent Puppet runs: `register_runner` skipped (`.credentials`
  exists) → nothing to notify → `delete_registration_token` skipped.
- If `register_runner` fails: `notify` does not fire (Puppet only
  refreshes subscribers when the resource completes successfully). Token
  lingers, but the instance is unhealthy, the `check-health` cron will
  mark it so, and the ASG replaces it — at which point the old
  deregistration Lambda (or the new one, depending on TF version)
  cleans up the secret as part of termination.
- If `delete_registration_token` itself fails (e.g. `AccessDenied`
  because old-Terraform lacks `DeleteSecret` IAM): Puppet reports the
  failure. One failed agent run per instance until Terraform is
  upgraded. Intentionally loud — we'd rather see the IAM gap than paper
  over it.

### 2.8 Puppet manifest wiring

Update `modules/profile/manifests/github_runner/service.pp` to manage:
- `/usr/local/bin/gha-on-runner-exit.sh`
- `/usr/local/bin/gha-lifecycle-heartbeater.sh`
- `/etc/systemd/system/gha-lifecycle-heartbeater.service`
- `/etc/systemd/system/gha-lifecycle-heartbeater.timer`
- enable + start the `.timer` unit.

### 2.9 Promote development → sandbox → global

After the Part 2 changes are working end-to-end in
`environments/development/` (verified by at least one successful
scale-in with a job in-flight), copy the same files to
`environments/sandbox/modules/profile/github_runner/`. After a bake
period in sandbox, copy to the top-level `modules/profile/github_runner/`
(global).

Each promotion is a separate PR (per recent repo convention). Don't
consolidate with the initial dev PR.

## Release & rollout (Part 2)

- Version bump per puppet-code's conventions.
- No toolkit prerequisites — scripts use `ih-aws autoscaling complete`
  (already released), `ih-ec2 tags`, and raw `aws` CLI for the rest.
- Must be deployed before Part 1's release reaches production ASGs, or
  at least simultaneously. If Part 1 ships without Part 2's
  `ExecStopPost`, the lifecycle hook will hang until 48h GlobalTimeout.

---

# What we rely on (already exists)

No new infrahouse-core or infrahouse-toolkit work is needed. Existing
pieces the Part 2 scripts use:

- **infrahouse-toolkit CLI:**
  - `ih-aws autoscaling complete --hook <name> [--result ...]`
    (cmd_autoscaling/cmd_complete/__init__.py:20) — auto-resolves
    instance_id/asg_name from IMDS via `ASGInstance`.
  - `ih-aws autoscaling scale-in enable-protection|disable-protection`
    — used today in `gha_prerun.sh` / `gha_postrun.sh`.
  - `ih-ec2 tags` — returns EC2 tags as JSON, parseable with
    `jq -r '."aws:autoscaling:groupName"'`.
- **Raw AWS CLI** for everything else we need:
  - `aws autoscaling describe-auto-scaling-instances` for lifecycle state.
  - `aws autoscaling record-lifecycle-action-heartbeat` for the heartbeater.
  - `aws secretsmanager delete-secret` for token cleanup (with `|| true`
    in Puppet for backwards compat).

Tradeoff of no new toolkit work: scripts call raw AWS CLI in a few
spots instead of going through typed Python wrappers. At this scope
that's a better simplicity/coverage tradeoff than adding, releasing, and
pinning new `ih-aws` subcommands.

---

# Open questions

- **Backward compatibility: new-Puppet + old-Terraform.** Because Part 2
  is deployed before Part 1 reaches production ASGs, the only realistic
  mixed state is Puppet ahead of Terraform. New Puppet-managed instances
  land on ASGs that are still using the old Lambda-driven deregistration
  and do not inject the `deregistration_hookname` fact.

  **Compatibility assessment: yes, backwards compatible.**
  - `deregistration_hookname` fact is empty, so `ExecStopPost` and the
    heartbeater attempt `ih-aws` calls with an empty `--hook` argument —
    the helper refuses, scripts no-op. Logs a warning but does no harm.
  - Old Lambda continues handling lifecycle completion via its existing
    path; instances still terminate cleanly.
  - **Token cleanup caveat (§2.7):** old-Terraform's instance profile
    lacks `secretsmanager:DeleteSecret`. The `delete_registration_token`
    exec fails loudly (intentional — we'd rather see the IAM gap than
    paper over it) on the first Puppet run after instance boot. Token
    cleanup falls back to the old Lambda on termination (same as today).
    Fixed by bumping the Terraform module version, which adds the IAM
    statement.
  - **Strictly an improvement over today's runtime behavior.** Today's
    baseline is "job dies in ~90s" — old `TimeoutStopSec=90s` +
    `KillMode=control-group` means systemd SIGKILLs the whole cgroup
    90s after `systemctl stop` fires. New Puppet removes the cgroup-
    wide SIGKILL, gives the listener graceful-shutdown semantics, and
    the new prerun tolerance means short jobs survive the race window
    entirely. Long jobs still get cut off when AWS terminates the
    instance (same as today), but through a cleaner path.
- **Integration test / promotion strategy.** Scale-in races are
  timing-sensitive and hard to reliably trigger in CI, so we rely on a
  staged rollout rather than an automated test:

  1. **terraform-aws-actions-runner dev fixture.** Test the successful
     path end-to-end against `environments/development`-synced puppet
     (`test_data/actions-runner/`): scale-in fires, runner exits cleanly,
     `ExecStopPost` completes the lifecycle hook, instance terminates.
     Done in this repo's test harness.
  2. **Promote puppet development → sandbox.** Copy the new
     github_runner files to `environments/sandbox/`. Upgrade the
     `terraform-aws-actions-runner` module version in the sandbox AWS
     account.
  3. **Observe sandbox for a week.** Real-world traffic, real jobs, real
     scale-in events. Watch for `ValidationError` in CloudTrail, stuck
     `Terminating:Wait` instances, and prerun failures. If the number
     trends to zero, proceed.
  4. **Promote puppet sandbox → global.** Copy to top-level `modules/`.
     Upgrade the terraform-aws-actions-runner version in all remaining
     environments (production, etc.).

  The one-week sandbox bake is the real test — it's the only realistic
  way to catch subtle interactions with actual GitHub dispatch patterns
  and long-running jobs.
- **Protection model stays as it is.** Protect at prerun, unprotect at
  postrun. That's correct for the common case. The race we're fixing
  only occurs in the narrow window between a postrun's unprotect and
  the next prerun's protect — scale-in picks a runner just before it
  picks up a new job. Parts 1–3 make *that window survivable* (prerun
  tolerates `Terminating:Wait`, runner finishes the job, `ExecStopPost`
  closes the hook). No broader redesign of how protection works. The
  state-aware `protect()` / `unprotect()` workstream is not needed —
  scope stays as drafted.

# Success criteria

- A test job that lands in the scale-in race window runs to completion
  (no exit 1 from prerun), postrun runs, runner exits via SIGTERM,
  `ExecStopPost` completes the lifecycle action, instance terminates.
- No `ValidationError` on `SetInstanceProtection` in CloudTrail.
- Heartbeater emits successful `RecordLifecycleActionHeartbeat` calls
  during long `Terminating:Wait` windows; lifecycle hook does not time
  out.
- GH #81 and Linear INF-1292 closed.
