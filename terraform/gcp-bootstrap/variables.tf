variable "config_path" {
  type        = string
  description = "ImageFactory config; project, zone, secret prefix and which flavors target GCP are read from it so they cannot drift"
  default     = "../../config/images.yaml"
}

variable "name_prefix" {
  type    = string
  default = "imagefactory"
}

variable "github_repository" {
  type        = string
  description = "GitHub \"org/repo\" allowed to sign in through Workload Identity Federation (no stored keys)"
}

variable "github_branch" {
  type        = string
  description = "Only runs on this branch may impersonate the builder service account"
  default     = "main"
}

variable "consumer_members" {
  type        = list(string)
  description = "IAM members (e.g. serviceAccount:app@proj.iam.gserviceaccount.com) that deploy VMs from golden images; they may use the images and read the stable pointers"
  default     = []
}
