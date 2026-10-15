variable "region" {
  type        = string
  description = "Region for the artifact bucket and KMS keys (the primary build region)"
  default     = "us-east-1"
}

variable "name_prefix" {
  type    = string
  default = "imagefactory"
}

variable "bucket_name" {
  type        = string
  description = "Globally unique S3 bucket for manifests and build evidence; set store.bucket in config/images.yaml to this"
}

variable "ssm_prefix" {
  type        = string
  description = "SSM parameter prefix for channel pointers (must match platforms.aws.ssm_prefix)"
  default     = "/imagefactory"
}

variable "builder_trusted_principal_arns" {
  type        = list(string)
  description = "IAM principals (e.g. an Azure DevOps agent's instance role) allowed to assume the builder role. Optional if github_repository is set."
  default     = []
}

variable "promoter_trusted_principal_arns" {
  type        = list(string)
  description = "IAM principals allowed to assume the promoter role. Optional if github_repository is set."
  default     = []
}

variable "github_repository" {
  type        = string
  description = "GitHub \"org/repo\" allowed to assume the roles through OIDC (no stored AWS keys). Empty disables GitHub trust."
  default     = ""
}

variable "github_branch" {
  type        = string
  description = "Only workflow runs on this branch may assume the builder role"
  default     = "main"
}

variable "github_oidc_provider_arn" {
  type        = string
  description = "Existing GitHub OIDC provider in this account (there can be only one). Empty creates it."
  default     = ""
}

variable "share_with_account_ids" {
  type        = list(string)
  description = "Accounts that may launch the AMIs; they are granted use of the EBS key"
  default     = []
}

variable "evidence_retention_days" {
  type        = number
  description = "Raw build artifacts (reports, vars) expire after this; manifests are kept"
  default     = 90
}

variable "smoke_instance_role_arns" {
  type        = list(string)
  description = "Role(s) behind the smoke-test instance profile (needs AmazonSSMManagedInstanceCore). Empty = no SSM join check."
  default     = []
}
