terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0"
    }
  }
}

# The promoter writes the channel pointers as tags on the image definition (one per flavor):
#   imagefactory-<channel>          = Azure gallery version (what we resolve here)
#   imagefactory-<channel>-version  = canonical ImageFactory version
data "azurerm_shared_image" "this" {
  name                = var.flavor
  gallery_name        = var.gallery_name
  resource_group_name = var.gallery_resource_group_name
}

locals {
  # Canonical "rhel9-20261112.1" -> gallery "2026.1112.1"; Jan 5 gives "2026.105.1" (no leading zero).
  # This is the same mapping as Version.azure in the CLI: change them together.
  pin_parts               = var.pin_version == null ? null : regex("^[a-z][a-z0-9]*-(\\d{4})(\\d{4})\\.(\\d+)$", var.pin_version)
  pinned_gallery_version  = var.pin_version == null ? null : join(".", [local.pin_parts[0], tonumber(local.pin_parts[1]), tonumber(local.pin_parts[2])])
  channel_gallery_version = try(data.azurerm_shared_image.this.tags["imagefactory-${var.channel}"], null)
  gallery_version         = coalesce(local.pinned_gallery_version, local.channel_gallery_version, "unset")
}

data "azurerm_shared_image_version" "this" {
  name                = local.gallery_version
  image_name          = var.flavor
  gallery_name        = var.gallery_name
  resource_group_name = var.gallery_resource_group_name

  lifecycle {
    precondition {
      condition     = local.gallery_version != "unset"
      error_message = "No ${var.channel} image has been published for ${var.flavor} yet (tag imagefactory-${var.channel} is missing on the image definition)."
    }
  }
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
