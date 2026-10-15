output "image_id" {
  description = "Gallery image version resource ID: use as source_image_id on azurerm_linux_virtual_machine / azurerm_windows_virtual_machine"
  value       = data.azurerm_shared_image_version.this.id
}

output "gallery_version" {
  description = "Compute Gallery version (Major.Minor.Patch form)"
  value       = local.gallery_version
}

output "version" {
  description = "Canonical ImageFactory version of the selected image"
  value       = var.pin_version != null ? var.pin_version : try(data.azurerm_shared_image.this.tags["imagefactory-${var.channel}-version"], local.gallery_version)
}

output "pinned" {
  value = var.pin_version != null
}
