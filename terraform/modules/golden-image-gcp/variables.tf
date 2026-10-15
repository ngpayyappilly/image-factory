variable "flavor" {
  type        = string
  description = "Image flavor, e.g. rhel9"
}

variable "project_id" {
  type        = string
  description = "Project that holds the channel secrets (platforms.gcp.project_id)"
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
  description = "Pin to an exact canonical version (e.g. rhel9-20261112.1). Leave null to follow the channel. Use only as an exception: pinned images do not receive updates."
  default     = null
  validation {
    condition     = var.pin_version == null || can(regex("^[a-z][a-z0-9]*-\\d{8}\\.\\d+$", var.pin_version))
    error_message = "pin_version must look like <flavor>-<YYYYMMDD>.<build>, e.g. rhel9-20261112.1."
  }
}

variable "image_project" {
  type        = string
  description = "Project that owns the images when pinning (defaults to project_id). Following a channel reads it from the pointer."
  default     = null
}

variable "secret_prefix" {
  type        = string
  description = "platforms.gcp.channel_secret_prefix"
  default     = "imagefactory"
}
