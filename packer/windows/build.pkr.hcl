build {
  name = "windows"
  sources = [
    "source.amazon-ebs.windows",
    "source.azure-arm.windows",
  ]

  # Select one platform per run:  packer build -only='windows.amazon-ebs.windows' ...

  provisioner "powershell" {
    script = "${path.root}/../scripts/windows/10-updates.ps1"
  }
  provisioner "windows-restart" {
    restart_timeout = "30m"
  }
  provisioner "powershell" {
    script = "${path.root}/../scripts/windows/20-harden.ps1"
    environment_vars = [
      "IMAGE_VERSION=${var.image_version}",
      "BASELINE_VERSION=${var.baseline_version}",
      "GIT_COMMIT=${var.git_commit}",
      "FLAVOR=${var.flavor}",
    ]
  }
  provisioner "windows-restart" {}

  provisioner "file" {
    source      = "${path.root}/../../tests/pester/"
    destination = "C:\\imagefactory-tests"
  }

  # Evidence for the gates, in the same JSON shapes the Linux build produces.
  provisioner "powershell" {
    script = "${path.root}/../scripts/windows/90-gates.ps1"
    environment_vars = [
      "SYFT_URL=${lookup(lookup(var.tool_urls, var.architecture, {}), "syft", "")}",
      "SYFT_SHA256=${lookup(lookup(var.tool_sha256, var.architecture, {}), "syft", "")}",
      "TRIVY_URL=${lookup(lookup(var.tool_urls, var.architecture, {}), "trivy", "")}",
      "TRIVY_SHA256=${lookup(lookup(var.tool_sha256, var.architecture, {}), "trivy", "")}",
    ]
    valid_exit_codes = [0, 1]
  }
  provisioner "file" {
    direction   = "download"
    source      = "C:\\imagefactory-reports\\"
    destination = "${var.report_dir}/${var.image_version}/${source.type}/"
  }

  # Platform-specific generalisation, last. The shared steps above are identical on both platforms.
  provisioner "powershell" {
    only   = ["amazon-ebs.windows"]
    script = "${path.root}/../scripts/windows/99-sysprep-aws.ps1"
    # EC2Launch shuts the instance down; the WinRM session ending is expected.
    valid_exit_codes = [0, 1, 2300218]
  }
  provisioner "powershell" {
    only   = ["azure-arm.windows"]
    script = "${path.root}/../scripts/windows/99-sysprep-azure.ps1"
  }

  post-processor "manifest" {
    output     = var.manifest_path
    strip_path = true
    custom_data = {
      image_version = var.image_version
    }
  }
}
