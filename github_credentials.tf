locals {
  # How the Lambdas authenticate to GitHub, derived once for all three of them (SEC-46).
  # type is "token" (arn is a secret with a classic token), "kms" (arn is a KMS key with the GitHub App's private key)
  # or "pem" (arn is a secret with the App's private key). A token wins over a PEM, as it always has;
  # terraform_data.github_credentials rejects every other combination.
  github_credential_type = (
    var.github_token_secret_arn != null ? "token" : (
      var.github_app_kms_key_arn != null ? "kms" : "pem"
    )
  )
  github_credentials = {
    type = local.github_credential_type
    arn = {
      token = var.github_token_secret_arn
      kms   = var.github_app_kms_key_arn
      pem   = var.github_app_pem_secret_arn
    }[local.github_credential_type]
  }
  github_credential_inputs_set = length(compact([
    var.github_token_secret_arn,
    var.github_app_pem_secret_arn,
    var.github_app_kms_key_arn,
  ]))
}

# The rules that compare credential inputs, checked at plan. A variable validation can't do it: a validation that
# reads another variable needs Terraform 1.9, and this module supports Terraform 1.5.
resource "terraform_data" "github_credentials" {
  lifecycle {
    precondition {
      condition     = local.github_credential_inputs_set > 0
      error_message = <<-EOT
        No GitHub credential is set. The Lambdas need one to register, deregister and count runners.
        Set github_app_kms_key_arn and github_app_id (recommended), github_app_pem_secret_arn and github_app_id,
        or github_token_secret_arn.
      EOT
    }
    precondition {
      condition = var.github_app_kms_key_arn == null ? true : (
        var.github_token_secret_arn == null && var.github_app_pem_secret_arn == null
      )
      error_message = <<-EOT
        github_app_kms_key_arn can't be combined with github_token_secret_arn or github_app_pem_secret_arn.
        With a KMS key, the Lambdas sign as the GitHub App through KMS, and must not be able to read a token or the
        App's private key. Remove the other credential input.
      EOT
    }
    precondition {
      # Only when the Lambdas authenticate as the App: a token in use, or no credential at all (the first rule), needs
      # no App ID.
      condition = (
        local.github_credentials.type != "token" && local.github_credentials.arn != null
      ) ? var.github_app_id != null : true
      error_message = <<-EOT
        github_app_id is required with github_app_kms_key_arn, and with github_app_pem_secret_arn unless
        github_token_secret_arn is set: the Lambdas authenticate as the GitHub App with this ID.
        Set it to the App ID from the App's settings page.
      EOT
    }
  }
}
