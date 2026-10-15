output "bucket_name" {
  value       = aws_s3_bucket.store.id
  description = "config/images.yaml: store.bucket"
}

output "signing_key_ref" {
  value       = "awskms:///alias/${var.name_prefix}-signing"
  description = "config/images.yaml: signing.key_ref"
}

output "ebs_kms_key_id" {
  value       = aws_kms_key.ebs.arn
  description = "config/images.yaml: platforms.aws.ebs_kms_key_id"
}

output "builder_role_arn" {
  value = aws_iam_role.builder.arn
}

output "promoter_role_arn" {
  value = aws_iam_role.promoter.arn
}

output "consumer_policy_arn" {
  value       = aws_iam_policy.consumer.arn
  description = "Attach to roles/users that deploy VMs from golden images"
}

output "github_actions_variables" {
  description = "Set these as GitHub repository variables (Settings > Secrets and variables > Actions > Variables)"
  value = {
    AWS_REGION        = var.region
    BUILDER_ROLE_ARN  = aws_iam_role.builder.arn
    PROMOTER_ROLE_ARN = aws_iam_role.promoter.arn
  }
}
