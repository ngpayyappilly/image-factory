# Everything ImageFactory needs on GCP: APIs, one Secret Manager secret per flavor and channel (the channel
# pointers), service accounts for the pipeline, GitHub sign-in through Workload Identity Federation (no stored
# keys), and a private network for boot tests. Names come from config/images.yaml.
locals {
  cfg    = yamldecode(file("${path.module}/${var.config_path}"))
  gcp    = local.cfg.platforms.gcp
  region = join("-", slice(split("-", local.gcp.zone), 0, 2))
  prefix = try(local.gcp.channel_secret_prefix, "imagefactory")

  flavors  = [for name, img in local.cfg.images : name if contains(img.platforms, "gcp")]
  channels = ["dev", "canary", "stable"]
  secrets = {
    for p in setproduct(local.flavors, local.channels) : "${p[0]}-${p[1]}" => {
      flavor  = p[0]
      channel = p[1]
      id      = "${local.prefix}-${p[0]}-${p[1]}"
    }
  }

  oidc         = "https://token.actions.githubusercontent.com"
  environments = ["imagefactory-canary", "imagefactory-stable", "imagefactory-maintenance"]
}

resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# --- channel pointers: one secret per flavor x channel, a new version per promotion ----------------
resource "google_secret_manager_secret" "channel" {
  for_each  = local.secrets
  secret_id = each.value.id
  labels    = { "imagefactory-managed" = "true" }
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# --- service accounts -----------------------------------------------------------------------
resource "google_service_account" "builder" {
  account_id   = "${var.name_prefix}-builder"
  display_name = "ImageFactory builder: builds images, boot-tests them, publishes to dev only"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "promoter" {
  account_id   = "${var.name_prefix}-promoter"
  display_name = "ImageFactory promoter: moves channels and retires images (approval environments only)"
  depends_on   = [google_project_service.apis]
}

# The build VM itself runs as this account, which has NO roles: a compromised build can reach nothing.
resource "google_service_account" "build_vm" {
  account_id   = "${var.name_prefix}-build-vm"
  display_name = "ImageFactory build VM (no permissions)"
  depends_on   = [google_project_service.apis]
}

# --- GitHub sign-in: Workload Identity Federation, nothing stored ------------------------------
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "ImageFactory GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  # Only this repository can ever get a token exchanged.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""
  oidc {
    issuer_uri = local.oidc
  }
}

# Builder: only workflow runs on the protected branch (a PR or feature branch cannot build or sign).
resource "google_service_account_iam_member" "builder_wif" {
  service_account_id = google_service_account.builder.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"
}

# Promoter: only jobs inside an approval environment, so Google refuses a promotion that skipped review.
resource "google_service_account_iam_member" "promoter_wif" {
  for_each           = toset(local.environments)
  service_account_id = google_service_account.promoter.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:environment:${each.key}"
}

# --- permissions ----------------------------------------------------------------------------
# Builder: create build/test VMs and images, and attach the no-permission build-VM account to them.
resource "google_project_iam_member" "builder" {
  for_each = toset(["roles/compute.instanceAdmin.v1", "roles/compute.storageAdmin"])
  project  = local.gcp.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.builder.email}"
}

resource "google_service_account_iam_member" "builder_acts_as_build_vm" {
  service_account_id = google_service_account.build_vm.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.builder.email}"
}

# The builder may add versions to the DEV secrets only. Per-secret IAM is how Google enforces
# "build publishes to dev, promotion publishes to canary/stable".
resource "google_secret_manager_secret_iam_member" "builder_dev" {
  for_each  = { for k, v in local.secrets : k => v if v.channel == "dev" }
  secret_id = google_secret_manager_secret.channel[each.key].id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = "serviceAccount:${google_service_account.builder.email}"
}

# Promoter: read and retire images, and add pointer versions to every channel.
resource "google_project_iam_custom_role" "promoter" {
  role_id     = "${replace(var.name_prefix, "-", "_")}_promoter"
  title       = "ImageFactory promoter"
  description = "Read images, delete retired images"
  permissions = ["compute.images.get", "compute.images.list", "compute.images.delete", "compute.globalOperations.get"]
}

resource "google_project_iam_member" "promoter" {
  project = local.gcp.project_id
  role    = google_project_iam_custom_role.promoter.id
  member  = "serviceAccount:${google_service_account.promoter.email}"
}

resource "google_secret_manager_secret_iam_member" "promoter" {
  for_each  = local.secrets
  secret_id = google_secret_manager_secret.channel[each.key].id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = "serviceAccount:${google_service_account.promoter.email}"
}

# --- consumers: use the images and read the stable pointers -----------------------------------
resource "google_project_iam_member" "consumer_images" {
  for_each = toset(var.consumer_members)
  project  = local.gcp.project_id
  role     = "roles/compute.imageUser"
  member   = each.key
}

resource "google_secret_manager_secret_iam_member" "consumer_stable" {
  for_each  = { for pair in setproduct(local.flavors, var.consumer_members) : "${pair[0]}|${pair[1]}" => pair }
  secret_id = google_secret_manager_secret.channel["${each.value[0]}-stable"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value[1]
}

# --- private network for boot tests: no external IPs, no inbound -------------------------------
resource "google_compute_network" "smoke" {
  name                    = "${var.name_prefix}-smoke"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "smoke" {
  name                     = "${var.name_prefix}-smoke"
  region                   = local.region
  network                  = google_compute_network.smoke.id
  ip_cidr_range            = "10.250.0.0/24"
  private_ip_google_access = true
}
