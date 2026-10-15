terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }
}

# The promoter adds a version to the secret <prefix>-<flavor>-<channel> on every promotion, holding
# {"image": "<gce image name>", "version": "<canonical>", "project": "<image project>"}.
# The latest version is the channel pointer, older versions are its history.
data "google_secret_manager_secret_version" "pointer" {
  count   = var.pin_version == null ? 1 : 0
  project = var.project_id
  secret  = "${var.secret_prefix}-${var.flavor}-${var.channel}"
}

locals {
  # The pointer names an image; it is not a secret, so unwrap the provider's sensitive marking.
  pointer = var.pin_version == null ? nonsensitive(jsondecode(data.google_secret_manager_secret_version.pointer[0].secret_data)) : null

  # Canonical "rhel9-20261112.1" -> GCE image name "rhel9-20261112-1" (the same mapping as Version.gcp in the CLI).
  image_name    = var.pin_version == null ? local.pointer.image : replace(var.pin_version, ".", "-")
  image_project = var.pin_version == null ? local.pointer.project : coalesce(var.image_project, var.project_id)
}

data "google_compute_image" "this" {
  name    = local.image_name
  project = local.image_project
}

check "pinned_image" {
  assert {
    condition     = var.pin_version == null
    error_message = "Golden image ${var.flavor} is PINNED to ${coalesce(var.pin_version, "-")}. Pinned versions stop receiving security updates and are deprecated 60 days after leaving the supported window."
  }
}

check "non_stable_channel" {
  assert {
    condition     = var.channel == "stable"
    error_message = "Golden image ${var.flavor} follows the ${var.channel} channel, which is not intended for production workloads."
  }
}
