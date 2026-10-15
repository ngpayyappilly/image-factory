variable "flavor" {
  type        = string
  description = "Image flavor, e.g. al2023, rhel9, win2022"
}

variable "channel" {
  type        = string
  description = "Channel to follow. Production workloads use stable; canary is for opted-in non-prod fleets."
  default     = "stable"
  validation {
    condition     = contains(["dev", "canary", "stable"], var.channel)
    error_message = "channel must be dev, canary or stable."
  }
}

variable "pin_version" {
  type        = string
  description = "Pin to an exact image version (e.g. al2023-20261112.1). Leave null to follow the channel. Use only as an exception: pinned images do not receive updates."
  default     = null
}

variable "image_owner_account_id" {
  type        = string
  description = "Account that owns the golden AMIs (only used when pinning)"
  default     = "self"
}

variable "ssm_prefix" {
  type    = string
  default = "/imagefactory"
}
