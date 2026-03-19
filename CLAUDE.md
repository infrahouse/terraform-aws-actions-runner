# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## First Steps

**Your first tool call in this repository MUST be reading .claude/CODING_STANDARD.md.
Do not read any other files, search, or take any actions until you have read it.**
This contains InfraHouse's comprehensive coding standards for Terraform, Python, and general formatting rules.

## Project Overview

This is an InfraHouse Terraform module (`terraform-aws-actions-runner`) that provisions GitHub Actions self-hosted runners on AWS using EC2 Auto Scaling Groups. It supports both GitHub token and GitHub App authentication, warm pools, spot instances, and automated runner lifecycle management via Lambda functions.

## Common Commands

```bash
make bootstrap          # Install Python dependencies and git hooks
make format             # Format all code (terraform fmt + black)
make lint               # Check formatting (black --check + terraform fmt -check)
make docs               # Regenerate README.md via terraform-docs
make test-keep          # Run integration tests, keep infrastructure after
make test-clean         # Run integration tests, destroy infrastructure after
make test-migration-keep   # Run migration tests, keep infrastructure
make test-migration-clean  # Run migration tests, destroy infrastructure
make clean              # Remove .terraform dirs, lockfiles, pytest cache
make release-patch      # Bump patch version, update CHANGELOG, tag
```

### Running a specific test

```bash
pytest -xvvs --aws-region=us-west-2 \
  --test-role-arn=arn:aws:iam::303467602807:role/actions-runner-tester \
  -k "test_name_pattern" \
  --github-token $GITHUB_TOKEN \
  tests/test_module.py
```

Tests require AWS credentials (via `--test-role-arn`) and a GitHub token (`--github-token`). They create real AWS infrastructure (EC2 instances, ASGs, Lambdas).

## Architecture

### Root Module

The root module creates:
- **Launch Template + ASG** (`main.tf`, `autoscaling.tf`) — EC2 instances with cloud-init provisioning via Puppet
- **Security Group** (`security_group.tf`) — network rules for runner instances
- **IAM Instance Profile** — via external `infrahouse/instance-profile/aws` module
- **CloudWatch Alarms** (`cloudwatch.tf`) — ASG health monitoring
- **Lifecycle Hooks** — three hooks: registration (on launch), bootstrap (on launch), deregistration (on terminate)

### Sub-modules (in `modules/`)

Each sub-module contains a Python Lambda function and EventBridge trigger:

- **`runner_registration/`** — Triggered by ASG launch lifecycle events. Registers the new EC2 instance as a GitHub Actions runner using the GitHub API. Stores registration tokens in Secrets Manager.
- **`runner_deregistration/`** — Triggered by ASG terminate lifecycle events. Removes the runner from GitHub before the instance is terminated. Uses `terraform-aws-lambda-monitored` for built-in error alerting.
- **`record_metric/`** — Runs on a schedule via EventBridge. Publishes custom CloudWatch metrics (e.g., idle runner count) for autoscaling decisions.

### Authentication Flow

The module supports two auth methods (configured via mutually exclusive variables):
1. **GitHub Token** (`github_token_secret_arn`) — classic PAT stored in Secrets Manager
2. **GitHub App** (`github_app_pem_secret_arn` + `github_app_id`) — generates temporary tokens from a PEM key

### External Dependencies

- `registry.infrahouse.com/infrahouse/instance-profile/aws` — IAM instance profile
- `registry.infrahouse.com/infrahouse/cloud-init/aws` — cloud-init/user data generation
- Provider requirements: Terraform ~> 1.5, AWS >= 5.31 < 7.0

## Conventions

- **Commit messages**: Conventional Commits format enforced by `hooks/commit-msg`. Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`, `security`.
- **Pre-commit hook**: Runs `terraform fmt -check` and updates terraform-docs. Installed via `make bootstrap`.
- **Formatting**: Terraform via `terraform fmt`, Python via `black`. Max line length 120 chars.
- **Releases**: Use `make release-patch|minor|major` (requires `git-cliff` and `bumpversion`). Must be on `main` branch.
- **Version tracking**: `.bumpversion.cfg` updates version in `README.md` and `locals.tf`.
- **Module registry**: CD pipeline publishes to `registry.infrahouse.com` on git tag push.

## Testing

Tests use `pytest` with the `pytest-infrahouse` plugin. Test infrastructure lives in `test_data/actions-runner/`. Tests are parametrized for AWS provider versions (5.x and 6.x) and authentication methods. The default test selector is `token-noble-aws-6` (set via `TEST_SELECTOR` in the Makefile).
