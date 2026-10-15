locals {
  common_tags = {
    "imagefactory:version"  = var.image_version
    "imagefactory:flavor"   = var.flavor
    "imagefactory:arch"     = var.architecture
    "imagefactory:baseline" = var.baseline_version
    "imagefactory:commit"   = var.git_commit
    "imagefactory:managed"  = "true"
  }
}

# --- AWS (primary) ------------------------------------------------------------------------
source "amazon-ebs" "linux" {
  # Build in the first region; Packer copies the AMI to the rest.
  region          = var.aws_regions[0]
  ami_regions     = slice(var.aws_regions, 1, length(var.aws_regions))
  ami_name        = var.image_version
  ami_description = "ImageFactory ${var.image_version} (baseline ${var.baseline_version})"
  ami_users       = var.aws_ami_users

  instance_type = var.aws_instance_type
  source_ami_filter {
    filters = {
      name                = var.aws_source_ami_name
      architecture        = var.architecture
      virtualization-type = "hvm"
      root-device-type    = "ebs"
    }
    owners      = [var.aws_source_ami_owner]
    most_recent = true
  }

  vpc_id        = var.aws_vpc_id == "" ? null : var.aws_vpc_id
  subnet_id     = var.aws_subnet_id == "" ? null : var.aws_subnet_id
  ssh_interface = var.aws_ssh_interface == "" ? null : var.aws_ssh_interface
  ssh_username  = var.aws_ssh_user

  temporary_security_group_source_public_ip = var.aws_sg_source_public_ip

  # Encrypted by default. Sharing across accounts requires a customer-managed key.
  encrypt_boot       = true
  kms_key_id         = var.aws_kms_key_id == "" ? null : var.aws_kms_key_id
  region_kms_key_ids = length(var.aws_region_kms_key_ids) == 0 ? null : var.aws_region_kms_key_ids

  # IMDSv2 only, on the build instance and as the AMI default.
  imds_support = "v2.0"
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  tags            = local.common_tags
  snapshot_tags   = local.common_tags
  run_tags        = merge(local.common_tags, { "Name" = "imagefactory-build-${var.image_version}" })
  run_volume_tags = local.common_tags
}

# --- Azure (optional) ---------------------------------------------------------------------
source "azure-arm" "linux" {
  use_azure_cli_auth = true
  subscription_id    = var.azure_subscription_id

  os_type         = "Linux"
  image_publisher = var.azure_image_publisher
  image_offer     = var.azure_image_offer
  image_sku       = var.azure_image_sku
  vm_size         = var.azure_vm_size

  build_resource_group_name = var.azure_build_resource_group
  shared_image_gallery_destination {
    subscription         = var.azure_subscription_id
    resource_group       = var.azure_gallery_resource_group
    gallery_name         = var.azure_gallery_name
    image_name           = var.flavor
    image_version        = var.azure_image_version
    replication_regions  = var.azure_replication_regions
    storage_account_type = "Standard_LRS"
  }
  azure_tags = local.common_tags
}

# --- GCP (optional) -----------------------------------------------------------------------
source "googlecompute" "linux" {
  project_id              = var.gcp_project_id
  zone                    = var.gcp_zone
  source_image_family     = var.gcp_source_image_family
  source_image_project_id = [var.gcp_source_image_project]
  machine_type            = var.gcp_machine_type
  ssh_username            = "packer"

  image_name              = var.gcp_image_name
  image_family            = var.flavor
  image_storage_locations = var.gcp_image_storage_locations
  image_labels = {
    imagefactory-version  = lower(replace(var.image_version, ".", "-"))
    imagefactory-flavor   = var.flavor
    imagefactory-baseline = lower(replace(var.baseline_version, ".", "-"))
  }
}

# --- VMware (optional): clone a pre-approved base template, publish to a Content Library ---
source "vsphere-clone" "linux" {
  vcenter_server      = var.vsphere_server
  username            = var.vsphere_user
  password            = var.vsphere_password
  insecure_connection = false

  datacenter = var.vsphere_datacenter
  cluster    = var.vsphere_cluster
  datastore  = var.vsphere_datastore
  folder     = var.vsphere_folder
  template   = var.vsphere_template
  vm_name    = "imagefactory-build-${var.image_version}"

  communicator   = "ssh"
  ssh_username   = var.vsphere_ssh_user
  ssh_agent_auth = true

  convert_to_template = false
  content_library_destination {
    library = var.vsphere_content_library
    name    = var.image_version
    ovf     = true
    destroy = true
  }
}
