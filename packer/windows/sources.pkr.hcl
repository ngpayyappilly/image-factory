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

source "amazon-ebs" "windows" {
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

  vpc_id    = var.aws_vpc_id == "" ? null : var.aws_vpc_id
  subnet_id = var.aws_subnet_id == "" ? null : var.aws_subnet_id

  # EC2Launch v2 runs the <powershell> user-data at first boot; Packer then talks WinRM over TLS.
  communicator   = "winrm"
  winrm_username = "Administrator"
  winrm_use_ssl  = true
  winrm_insecure = true
  winrm_timeout  = "30m"
  user_data_file = "${path.root}/../scripts/windows/00-winrm-bootstrap.ps1"

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 50
    volume_type           = "gp3"
    delete_on_termination = true
  }

  temporary_security_group_source_public_ip = var.aws_sg_source_public_ip

  encrypt_boot       = true
  kms_key_id         = var.aws_kms_key_id == "" ? null : var.aws_kms_key_id
  region_kms_key_ids = length(var.aws_region_kms_key_ids) == 0 ? null : var.aws_region_kms_key_ids

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

source "azure-arm" "windows" {
  use_azure_cli_auth = true
  subscription_id    = var.azure_subscription_id

  os_type         = "Windows"
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

  communicator   = "winrm"
  winrm_use_ssl  = true
  winrm_insecure = true
  winrm_timeout  = "30m"
  winrm_username = "packer"

  azure_tags = local.common_tags
}
