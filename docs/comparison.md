# Comparison with Alternatives

This page compares the InfraHouse module with the popular community module 
[github-aws-runners/terraform-aws-github-runner](https://github.com/github-aws-runners/terraform-aws-github-runner).

## Quick Comparison

| Feature | InfraHouse | Community Module |
|---------|------------|------------------|
| **Architecture** | ASG-native | Lambda-managed EC2 |
| **Warm Pool** | ✅ Native | ❌ Not supported |
| **Webhook Scaling** | ❌ Polling-based | ✅ Instant |
| **Ephemeral Runners** | ❌ | ✅ |
| **Windows Support** | ❌ | ✅ |
| **Puppet Integration** | ✅ | ❌ |
| **Lambda Monitoring** | ✅ ISO 27001 compliant | ❌ Basic logging |
| **Complexity** | Lower | Higher |

## Architecture Differences

### InfraHouse Approach

```
CloudWatch Metric (idle runners)
         │
         ▼
CloudWatch Alarm
         │
         ▼
ASG Scaling Policy
         │
         ▼
Launch from Warm Pool (seconds) or Cold Start (minutes)
```

**Pros:**
- Uses AWS primitives (ASG, CloudWatch)
- Warm pool provides fast scaling
- Simpler to debug and operate
- Fewer moving parts

**Cons:**
- Polling-based (1-5 minute delay)
- No ephemeral runner support
- Linux only

### Community Module Approach

```
GitHub webhook (workflow_job event)
         │
         ▼
API Gateway → Webhook Lambda
         │
         ▼
SQS Queue (30 sec delay)
         │
         ▼
Scale-up Lambda → Creates EC2 directly
```

**Pros:**
- Near-instant scaling (~30 seconds)
- Ephemeral runners (better security)
- Windows support
- More features (GHES, multi-runner, etc.)

**Cons:**
- Complex architecture (API GW, SQS, EventBridge, multiple Lambdas)
- No warm pool support
- More components to monitor and debug

## When to Use Each

### Use InfraHouse When:

- You use **Puppet** for configuration management
- You need **warm pool** for fast instance availability
- You want **simpler operations** with fewer components
- You need **ISO 27001 compliant** Lambda monitoring out of the box
- Your jobs can tolerate **1-5 minute** initial queue time
- You're already using the **InfraHouse module ecosystem**

### Use Community Module When:

- You need **Windows runners**
- You need **ephemeral runners** for security isolation
- You need **instant scaling** via webhooks
- You need **GitHub Enterprise Server** support
- You need **repository-level** runners (not just org-level)
- You need **multi-runner** configurations in one deployment

## Feature Deep Dive

### Warm Pool vs No Warm Pool

**InfraHouse (with warm pool):**
```
Job queued → Metric published → Alarm fires → Wake hibernated instance → 30 seconds
```

**Community (cold start):**
```
Job queued → Webhook received → Lambda creates EC2 → Boot + configure → 2-5 minutes
```

The community module's "pool" feature is different — it maintains running instances on a schedule, not hibernated ones.

### Compliance & Monitoring

**InfraHouse:**
- All Lambdas wrapped in `terraform-aws-lambda-monitored`
- Automatic error alerting via SNS
- Throttle monitoring
- Configurable log retention
- Designed for Vanta/ISO 27001 audits

**Community:**
- Basic CloudWatch logging
- No built-in alerting
- Would need to add monitoring layer for compliance

### Puppet Integration

**InfraHouse:**
```hcl
module "actions-runner" {
  # Native Puppet support
  puppet_hiera_config_path = "/opt/puppet/hiera.yaml"
  puppet_module_path       = "/opt/puppet/modules"
  packages                 = ["infrahouse-puppet-data"]
}
```

**Community:**
```hcl
module "runners" {
  # Shell script only
  userdata_pre_install  = file("./scripts/pre-install.sh")
  userdata_post_install = file("./scripts/post-install.sh")
}
```

## Migration Considerations

### From Community to InfraHouse

If you're considering switching:

1. **Evaluate features** — Do you need ephemeral/Windows? Stay with community.
2. **Evaluate operations** — Is debugging complex? Consider InfraHouse.
3. **Evaluate compliance** — Need Lambda monitoring? InfraHouse has it built-in.

### The Fork Trap

Some teams consider forking the community module to add warm pool. This is problematic:

- Community module doesn't use ASG for scaling — it creates EC2 directly
- Adding warm pool would require rewriting core architecture
- You'd maintain 3,000+ commits of someone else's code
- Every upstream release requires merge work

If you need warm pool, InfraHouse is purpose-built for it.

## Summary

Both modules solve the same problem differently:

| If you value... | Choose... |
|-----------------|-----------|
| Feature completeness | Community |
| Operational simplicity | InfraHouse |
| Instant scaling | Community |
| Fast recovery (warm pool) | InfraHouse |
| Puppet integration | InfraHouse |
| Windows support | Community |
| Compliance monitoring | InfraHouse |
