output "ami_id" {
  description = "AMI to launch from, in the provider's region"
  value       = var.pin_version == null ? data.aws_ssm_parameter.channel_ami[0].value : data.aws_ami.pinned[0].id
}

output "version" {
  description = "Canonical ImageFactory version of the selected image"
  value       = var.pin_version == null ? data.aws_ssm_parameter.channel_version[0].value : var.pin_version
}

output "pinned" {
  value = var.pin_version != null
}
