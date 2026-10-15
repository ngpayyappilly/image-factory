output "image_self_link" {
  description = "Use as the boot disk image on google_compute_instance (initialize_params.image)"
  value       = data.google_compute_image.this.self_link
}

output "image_name" {
  description = "GCE image name (the version with dots replaced by hyphens)"
  value       = local.image_name
}

output "version" {
  description = "Canonical ImageFactory version of the selected image"
  value       = var.pin_version != null ? var.pin_version : local.pointer.version
}

output "pinned" {
  value = var.pin_version != null
}
