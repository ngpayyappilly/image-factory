terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

# Default path: follow the channel. Nothing to maintain; a new stable image is picked up at the
# next plan/replace. Pinning is the exception and is deliberately loud (see the check below).
data "aws_ssm_parameter" "channel_ami" {
  count = var.pin_version == null ? 1 : 0
  name  = "${var.ssm_prefix}/${var.flavor}/${var.channel}/ami_id"
}

data "aws_ssm_parameter" "channel_version" {
  count = var.pin_version == null ? 1 : 0
  name  = "${var.ssm_prefix}/${var.flavor}/${var.channel}/version"
}

data "aws_ami" "pinned" {
  count       = var.pin_version == null ? 0 : 1
  owners      = [var.image_owner_account_id]
  most_recent = true
  filter {
    name   = "tag:imagefactory:version"
    values = [var.pin_version]
  }
  filter {
    name   = "tag:imagefactory:flavor"
    values = [var.flavor]
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
