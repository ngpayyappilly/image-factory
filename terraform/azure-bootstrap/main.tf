# Everything ImageFactory needs on Azure: the Compute Gallery and one image definition per flavor, the
# resource groups for builds and boot tests, a private network for the smoke tests, and two identities
# (builder, promoter) that GitHub signs in to with OIDC. Names come from config/images.yaml.
locals {
  cfg   = yamldecode(file("${path.module}/${var.config_path}"))
  azure = local.cfg.platforms.azure

  # Flavors that target Azure, with what the image definition needs.
  flavors = {
    for name, img in local.cfg.images : name => {
      os_type = img.os_family == "windows" ? "Windows" : "Linux"
    } if contains(img.platforms, "azure")
  }

  regions      = local.azure.replication_regions
  oidc         = "https://token.actions.githubusercontent.com"
  environments = ["imagefactory-canary", "imagefactory-stable", "imagefactory-maintenance"]
}

resource "azurerm_resource_group" "gallery" {
  name     = local.azure.gallery_resource_group
  location = local.regions[0]
}

resource "azurerm_resource_group" "build" {
  name     = local.azure.build_resource_group
  location = local.regions[0]
}

resource "azurerm_resource_group" "smoke" {
  name     = local.azure.smoke_test.resource_group
  location = local.regions[0]
}

resource "azurerm_shared_image_gallery" "this" {
  name                = local.azure.gallery_name
  resource_group_name = azurerm_resource_group.gallery.name
  location            = azurerm_resource_group.gallery.location
  description         = "ImageFactory golden images"
}

# Packer publishes versions into these; it does not create the definitions. Channel pointers are tags on them.
resource "azurerm_shared_image" "flavor" {
  for_each            = local.flavors
  name                = each.key
  gallery_name        = azurerm_shared_image_gallery.this.name
  resource_group_name = azurerm_resource_group.gallery.name
  location            = azurerm_resource_group.gallery.location
  os_type             = each.value.os_type
  hyper_v_generation  = "V2"
  architecture        = "x64"

  identifier {
    publisher = var.image_publisher
    offer     = each.key
    sku       = "gen2"
  }

  # Channel tags (imagefactory-stable, ...) are written by the promoter; do not fight them.
  lifecycle {
    ignore_changes = [tags]
  }
}

# --- private network for boot tests: no public IPs, no inbound -----------------------------
resource "azurerm_virtual_network" "smoke" {
  for_each            = { for i, r in local.regions : r => i }
  name                = "${var.name_prefix}-smoke-${each.key}"
  resource_group_name = azurerm_resource_group.smoke.name
  location            = each.key
  address_space       = ["10.250.${each.value}.0/24"]
}

resource "azurerm_subnet" "smoke" {
  for_each             = azurerm_virtual_network.smoke
  name                 = "smoke"
  resource_group_name  = azurerm_resource_group.smoke.name
  virtual_network_name = each.value.name
  address_prefixes     = each.value.address_space
}

# --- identities: GitHub signs in with OIDC, nothing stored ----------------------------------
resource "azurerm_user_assigned_identity" "builder" {
  name                = "${var.name_prefix}-builder"
  resource_group_name = azurerm_resource_group.gallery.name
  location            = azurerm_resource_group.gallery.location
}

resource "azurerm_user_assigned_identity" "promoter" {
  name                = "${var.name_prefix}-promoter"
  resource_group_name = azurerm_resource_group.gallery.name
  location            = azurerm_resource_group.gallery.location
}

# Builder: only workflow runs on the protected branch (a PR or feature branch cannot build or sign).
resource "azurerm_federated_identity_credential" "builder" {
  name                = "github-${var.github_branch}"
  resource_group_name = azurerm_resource_group.gallery.name
  parent_id           = azurerm_user_assigned_identity.builder.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = local.oidc
  subject             = "repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"
}

# Promoter: only jobs inside an approval environment, so Azure refuses a promotion that skipped review.
resource "azurerm_federated_identity_credential" "promoter" {
  for_each            = toset(local.environments)
  name                = "github-env-${each.key}"
  resource_group_name = azurerm_resource_group.gallery.name
  parent_id           = azurerm_user_assigned_identity.promoter.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = local.oidc
  subject             = "repo:${var.github_repository}:environment:${each.key}"
}

# --- permissions ----------------------------------------------------------------------------
# Builder: build VMs in the build group, write versions to the gallery, run boot tests.
resource "azurerm_role_assignment" "builder_build" {
  scope                = azurerm_resource_group.build.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.builder.principal_id
}

resource "azurerm_role_assignment" "builder_gallery" {
  scope                = azurerm_shared_image_gallery.this.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.builder.principal_id
}

resource "azurerm_role_assignment" "builder_smoke" {
  scope                = azurerm_resource_group.smoke.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.builder.principal_id
}

# The builder can set the dev channel only by virtue of the CLI; Azure RBAC cannot scope a tag KEY. The hard
# separation is the identity itself: only the promoter is reachable from approval environments.
resource "azurerm_role_definition" "promoter" {
  name        = "${var.name_prefix}-promoter"
  scope       = azurerm_shared_image_gallery.this.id
  description = "Read the gallery, move channel tags, remove retired versions"

  permissions {
    actions = [
      "Microsoft.Compute/galleries/read",
      "Microsoft.Compute/galleries/images/read",
      "Microsoft.Compute/galleries/images/versions/read",
      "Microsoft.Compute/galleries/images/versions/delete",
      "Microsoft.Resources/tags/*",
    ]
  }
  assignable_scopes = [azurerm_shared_image_gallery.this.id]
}

resource "azurerm_role_assignment" "promoter" {
  scope              = azurerm_shared_image_gallery.this.id
  role_definition_id = azurerm_role_definition.promoter.role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.promoter.principal_id
}

# Consumers (teams deploying VMs) need to read versions and the channel tags.
resource "azurerm_role_definition" "consumer" {
  name        = "${var.name_prefix}-consumer"
  scope       = azurerm_shared_image_gallery.this.id
  description = "Resolve the stable golden image"

  permissions {
    actions = [
      "Microsoft.Compute/galleries/read",
      "Microsoft.Compute/galleries/images/read",
      "Microsoft.Compute/galleries/images/versions/read",
    ]
  }
  assignable_scopes = [azurerm_shared_image_gallery.this.id]
}
