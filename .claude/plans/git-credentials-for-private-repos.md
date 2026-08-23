# Plan: transparent git credentials for private repos on self-hosted runners

**Status:** Not implemented.
**Spans three repositories.** Puppet side has a companion plan:
`puppet-code/.claude/plans/actions-runner-git-credentials.md`.

## Problem

A job on a self-hosted runner can check out the repository it was triggered for — `actions/checkout` uses the
per-repo `GITHUB_TOKEN`. It cannot reach *any other* private repository in the same organisation.

This bites hardest where the dependency is resolved by a tool that shells out to git rather than by
`actions/checkout`:

- `terraform init` pulling a private module from another repository in the org
- `go mod download` against a private module path
- `git submodule update`
- `pip install git+https://github.com/org/private-lib`

Today every affected workflow carries a variant of this:

```yaml
- name: Configure Git for Private Modules
  env:
    APP_TOKEN: ${{ steps.app-token.outputs.token }}
  run: |
    GIT_CFG="$RUNNER_TEMP/.gitconfig"
    : > "$GIT_CFG"
    git config --file "$GIT_CFG" \
      url."https://x-access-token:${APP_TOKEN}@github.com/".insteadOf "https://github.com/"
    echo "GIT_CONFIG_GLOBAL=$GIT_CFG" >> "$GITHUB_ENV"
```

Copied per repository, paired with an `actions/create-github-app-token` step, with the App id and private key
plumbed into every repository's secrets. It is boilerplate that has to be right in every consumer.

## Goal

A job on the runner can `git clone` any repository the organisation chooses to allow, with **no workflow
changes at all** — no token step, no `insteadOf`, no per-repository secrets.

## Design

Install a **git credential helper** on the runner and register it in the system gitconfig:

```
git config --system credential."https://github.com".helper /usr/local/bin/gha-git-credential
git config --system credential."https://github.com".useHttpPath true
```

git invokes the helper with `protocol=https`, `host=github.com`, and (because of `useHttpPath`)
`path=<org>/<repo>` on stdin, and reads back:

```
username=x-access-token
password=<short-lived installation token>
```

Every tool that shells out to git is covered by one mechanism. Nothing is written to a workflow-visible file,
nothing lands in the environment, and no workflow mentions credentials.

`useHttpPath` gives the helper the repository name, which is what makes per-repository token scoping possible
later without another redesign.

### Where the token comes from — the actual decision

The module today **deliberately prevents the runner from reading the App PEM.** `data_sources.tf` scopes the
instance profile's Secrets Manager access to `${local.registration_token_secret_prefix}-*`: the runner can fetch
its own registration token and nothing else. The PEM is granted only to the Lambdas. Any design here has to
either preserve that boundary or consciously break it.

Three options were considered:

**A. Widen the instance profile to read the App PEM.** One line, no new infrastructure. Also puts the
organisation's App private key on every runner, where any job can read it and mint unlimited tokens for anything
the App can reach, indefinitely, with no expiry. **Rejected.**

**B. A second, read-only GitHub App whose PEM the runner may read.** Simpler than C — no new Lambda. Blast
radius is bounded to `contents: read`, which is what is being granted anyway. But it still leaves a long-lived,
portable credential on the instance: anyone who exfiltrates the file keeps organisation-wide read access until
somebody notices and rotates it. **Viable fallback if the Lambda is unwanted; not the recommendation.**

**C. A token-minting Lambda the runner invokes through its IAM role.** The PEM stays exactly where it already
is. The only credential on the instance is the instance profile itself — rotating, instance-bound, and not
portable off the host. The Lambda returns a token with a one-hour lifetime and `contents: read` only.
**Chosen.**

The practical difference between B and C is not "can a running job read private repos" — under all three it can,
by design. It is what an attacker keeps *after* the job ends and the disk is imaged.

# Part 1 — `terraform-aws-actions-runner` (this repo)

## 1.1 New submodule `modules/git_credentials/`

Mirror the existing submodule pattern (`runner_registration/`, `record_metric/`): a Python Lambda behind
`registry.infrahouse.com/infrahouse/lambda-monitored/aws`, its own IAM policy, its own alarms.

Handler contract — input:

```json
{"repository": "org/some-private-repo"}
```

output:

```json
{"token": "ghs_...", "expires_at": "2026-08-23T16:00:00Z"}
```

Behaviour:

1. If `REPO_ALLOWLIST` is non-empty, reject any `repository` outside it before doing anything else.
2. Branch on `GITHUB_SECRET_TYPE`, exactly as `record_metric` and the two lifecycle Lambdas already do:
   - **`pem`** — resolve the App installation for `GITHUB_ORG_NAME` and mint a token with
     `permissions: {"contents": "read"}` and `repositories: [<repo>]`.
   - **`token`** — return the classic PAT from Secrets Manager. It is already a valid git credential.

Note on the API: `PyGithub`'s `GithubIntegration.get_access_token(installation_id, permissions=...)` supports
`permissions` but **not** `repositories`. Scoping needs a direct
`POST /app/installations/{id}/access_tokens` call. See 2.1.

### The two auth modes are not equally safe, and that is worth saying in the README

Under **App authentication** the credential handed to a job is minted per request: read-only, one repository,
one hour. Under **classic PAT authentication** there is nothing to mint — the Lambda hands back the PAT
itself, with whatever scopes it was created with, which in practice means `repo` and therefore **write** access
to everything the token owner can reach, with no expiry.

This is not a reason to add an enable flag. It is a reason for the README to say plainly that pools using
`github_token_secret_arn` should move to `github_app_pem_secret_arn`, and for `git_credentials_repo_allowlist`
to be treated as close to mandatory on PAT pools.

IAM for the Lambda: `secretsmanager:GetSecretValue` on `var.github_app_pem_secret_arn` only — identical to what
`record_metric` already has.

## 1.2 Instance profile — one new statement

In `data_sources.tf`, alongside the existing Secrets Manager statement:

```hcl
statement {
  actions   = ["lambda:InvokeFunction"]
  resources = [module.git_credentials.lambda_function_arn]
}
```

Scoped to that single function. The runner gains no new Secrets Manager access.

## 1.3 Pass configuration to Puppet as custom facts

Same mechanism as `registration_token_secret_prefix` and `bootstrap_hookname` in `main.tf`'s `custom_facts`:

```hcl
git_credentials_lambda = module.git_credentials.lambda_function_name
```

Always set. There is no enable flag: a runner that cannot clone the organisation's private repositories is
the defect this plan exists to fix, and making it opt-in would leave every consumer writing the boilerplate
this is meant to delete. Older module versions do not emit the fact at all, so Puppet still has to tolerate
its absence during rollout — see Part 3.

## 1.4 Variables

```hcl
variable "git_credentials_repo_allowlist" {
  description = <<-EOT
    If non-empty, only these repositories (owner/name) may be cloned through the
    credential helper. Empty means any repository the App installation can read.
  EOT
  type        = list(string)
  default     = []
}
```

The allowlist stays a variable because it is a security bound, not a convenience toggle — it is the only knob
that narrows an otherwise organisation-wide grant, and there is no default value that is right for everyone.
Empty means "whatever the App installation can read", which is the same reach the workflows have today via
`actions/create-github-app-token`.

### This is a behaviour change for every consumer

With no enable flag, upgrading the module gives every job on every existing pool read access to the
organisation's private repositories. That is the point of the change, but it is not something to ship quietly:

- **Major version bump.** Not a minor. The grant is new even though no input changed.
- **CHANGELOG must lead with it**, naming the new capability and pointing at
  `git_credentials_repo_allowlist` for operators who want it narrowed.
- **Call out the untrusted-PR case by name** in the release note, not just in the README.

Anyone running untrusted pull requests on a self-hosted pool needs to know before they upgrade, because for
them this converts an existing bad practice into a much more expensive one.

## 1.5 Test fixtures

In `test_data/actions-runner/`, add a case that enables the feature and asserts a job on the runner can clone a
second private repository it was not triggered for. Per repo convention, remember to pass any new root outputs
through the fixture's `outputs.tf`.

## 1.6 Docs

Regenerate `README.md` via `make docs`. Add a section covering what the feature grants, the security note from
below, and how to restrict with the allowlist.

# Part 2 — `infrahouse-core` and `infrahouse-toolkit`

## 2.1 `infrahouse-core` — scoped token minting

`get_tmp_token()` currently returns an unscoped installation token: full App permissions, all repositories. Add
a sibling rather than changing its behaviour, since the Lambdas depend on the current semantics:

```python
def get_scoped_tmp_token(
    gh_app_id: int,
    pem_key_secret: str,
    github_org_name: str,
    permissions: dict = None,
    repositories: list = None,
    region: str = None,
    role_arn: str = None,
) -> tuple:
    """Return (token, expires_at) for a narrowly scoped installation token."""
```

Implemented against `POST /app/installations/{id}/access_tokens` directly, reusing the existing installation
lookup. Type hints and RST docstrings per the coding standard.

## 2.2 `infrahouse-toolkit` — the helper itself

Add `ih-github credential-helper` under `infrahouse_toolkit/cli/ih_github/`, alongside the existing
`cmd_runner`. It is already the CLI present on every runner.

It must speak git's credential protocol: read key=value lines from stdin, and for the `get` operation write
`username` and `password` to stdout. Any other operation (`store`, `erase`) exits 0 silently.

Caching is required, not optional. A `terraform init` resolving twenty modules will invoke the helper twenty
times; without a cache that is twenty Lambda invocations and twenty GitHub API calls per job.

- Cache in `/dev/shm` (tmpfs — never touches disk), one file per repository scope, mode `0600`, owned by the
  runner user.
- Treat a cached token as valid until 10 minutes before `expires_at`, then re-mint. Long jobs outlive the
  one-hour token, and git re-invokes the helper per operation, so refresh happens naturally.
- The postrun hook clears the cache directory (Part 3).

Cold-start latency on the Lambda is paid once per job, not per git operation. Provisioned concurrency is not
warranted.

# Part 3 — `puppet-code`

Detailed in the companion plan. Summary of what it provides:

- `/usr/local/bin/gha-git-credential` — thin wrapper invoking `ih-github credential-helper`.
- System gitconfig registering the helper for `https://github.com`, conditional on the
  `git_credentials_lambda` fact being non-empty.
- Cache teardown in `gha_postrun.sh`.

## Security model — state this explicitly in the README

**Any job running on this pool can obtain read access to every repository the GitHub App installation can see**
(narrowed by `git_credentials_repo_allowlist` where set). That is not a flaw in the design; it is what
"this runner can clone private repositories" means. No arrangement of tokens changes it.

What the design does bound:

| | |
|---|---|
| permission | `contents: read` only — no write, no admin, no secrets, no Actions |
| lifetime | one hour, refreshed on demand |
| persistence | tmpfs only; nothing on disk, nothing in workflow environment |
| portability | the on-instance credential is an IAM role, not an exportable key |
| reach | optionally restricted to an explicit repository allowlist |

**Do not enable this on a pool that runs untrusted pull requests.** GitHub already advises against self-hosted
runners for public/untrusted workflows; this raises the consequence of that mistake from "the runner is
compromised" to "every private repository in the organisation has been read."

## Alternative worth considering alongside, not instead

For the private-Terraform-module case specifically, a private module registry sidesteps git authentication
entirely: module sources become `registry.example.com/org/name/aws`, authenticated by a single
`TF_TOKEN_registry_example_com` that Puppet writes once. Narrower than this plan — it does nothing for `go get`,
submodules, or pip — but materially less machinery for that one use case.

The two compose: registry for Terraform modules, credential helper for everything else.

## Testing

1. Unit: helper speaks the credential protocol correctly, including malformed stdin and non-`get` operations.
2. Unit: allowlist rejects a repository outside it before any token is minted.
3. Integration: a job clones a private repository in the org that it was not triggered for.
4. Integration: with an allowlist set, a repository outside it fails and no token is minted.
5. Cache: a second git operation in the same job does not invoke the Lambda.
6. Both auth modes: a `pem` pool receives a minted read-only token; a `token` pool receives the PAT.
7. Compatibility: a runner built from an older module version (no `git_credentials_lambda` fact) still
   converges and still runs jobs — the helper is simply not installed.

## Rollout

1. `infrahouse-core` — `get_scoped_tmp_token`, released.
2. `infrahouse-toolkit` — `credential-helper` subcommand, released.
3. `puppet-code` — development, then sandbox, then global, following the established promotion path. Puppet
   ships **before** the Terraform side, so that by the time the fact starts appearing the code that consumes
   it is already everywhere. Until then Puppet sees no fact and does nothing.
4. This module — submodule, IAM, fact, allowlist variable. **Major version**, with the CHANGELOG note above.
5. Roll one pool to the new major, confirm a cross-repository clone, then widen.

Every consumer picks this up when they take the major version. That is the intended behaviour, and the reason
the version bump and release note carry the warning rather than a variable default.
