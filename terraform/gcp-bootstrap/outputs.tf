output "github_actions_variables" {
  description = "Set these as GitHub repository variables. The workflows sign in to Google Cloud only when GCP_WORKLOAD_IDENTITY_PROVIDER is set."
  value = {
    GCP_WORKLOAD_IDENTITY_PROVIDER = google_iam_workload_identity_pool_provider.github.name
    GCP_BUILDER_SERVICE_ACCOUNT    = google_service_account.builder.email
    GCP_PROMOTER_SERVICE_ACCOUNT   = google_service_account.promoter.email
  }
}

output "build_vm_service_account" {
  description = "config/images.yaml: platforms.gcp.build_service_account"
  value       = google_service_account.build_vm.email
}

output "smoke_test_subnetwork" {
  description = "config/images.yaml: platforms.gcp.smoke_test.subnetwork"
  value       = google_compute_subnetwork.smoke.id
}

output "channel_secrets" {
  description = "One secret per flavor and channel; each promotion adds a version"
  value       = [for s in google_secret_manager_secret.channel : s.secret_id]
}
