variable "subscription_id" {
  type        = string
  description = "Subscription that holds the gallery (platforms.azure.subscription_id)"
}

variable "config_path" {
  type        = string
  description = "ImageFactory config; resource group, gallery and region names are read from it so they cannot drift"
  default     = "../../config/images.yaml"
}

variable "name_prefix" {
  type    = string
  default = "imagefactory"
}

variable "github_repository" {
  type        = string
  description = "GitHub \"org/repo\" allowed to sign in through OIDC (no stored Azure secrets)"
}

variable "github_branch" {
  type        = string
  description = "Only runs on this branch may use the builder identity"
  default     = "main"
}

variable "image_publisher" {
  type        = string
  description = "Gallery image-definition publisher (our own namespace, not the marketplace source image)"
  default     = "ImageFactory"
}
