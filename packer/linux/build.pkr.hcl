build {
  name = "linux"
  sources = [
    "source.amazon-ebs.linux",
    "source.azure-arm.linux",
    "source.googlecompute.linux",
    "source.vsphere-clone.linux",
  ]

  # Select one platform per run:  packer build -only='linux.amazon-ebs.linux' ...

  provisioner "shell" {
    inline = [
      "cloud-init status --wait || true",
    ]
  }

  # Shared hardening: identical for every platform. Platform differences stay in the sources above.
  provisioner "ansible" {
    playbook_file = "${path.root}/../../ansible/linux.yml"
    use_proxy     = false
    ansible_env_vars = [
      "ANSIBLE_ROLES_PATH=${path.root}/../../ansible/roles:${path.root}/../../ansible/external",
      "ANSIBLE_COLLECTIONS_PATH=${path.root}/../../ansible/external",
      "ANSIBLE_HOST_KEY_CHECKING=False",
      # Per-task timing summary at the end of the run: the CIS role dominates the build and we want to see where the time goes.
      "ANSIBLE_CALLBACKS_ENABLED=ansible.posix.profile_tasks",
      "PROFILE_TASKS_TASK_OUTPUT_LIMIT=25",
    ]
    extra_arguments = [
      "--extra-vars", "flavor=${var.flavor} cis_role=${var.cis_role} cis_vars=${var.cis_vars} distro_family=${var.distro_family} baseline_version=${var.baseline_version} image_version=${var.image_version} architecture=${var.architecture} git_commit=${var.git_commit} distro_releasever=${var.distro_releasever}",
    ]
  }

  # Strip machine identity BEFORE tests and scans so they see exactly what ships.
  provisioner "shell" {
    execute_command = "sudo -E bash '{{ .Path }}'"
    script          = "${path.root}/../scripts/linux/cleanup.sh"
  }

  # Packer uploads a directory's contents into an EXISTING directory. Without this mkdir every test file was written over a
  # single file called /tmp/imagefactory-goss and goss then failed with "not a directory" (found on the first real build).
  provisioner "shell" {
    inline = ["mkdir -p /tmp/imagefactory-goss"]
  }

  provisioner "file" {
    source      = "${path.root}/../../tests/goss/"
    destination = "/tmp/imagefactory-goss/"
  }

  # Gate evidence: functional tests, CIS audit, vulnerability + secret scan, SBOM.
  provisioner "shell" {
    # A custom execute_command REPLACES Packer's default, and only {{ .Vars }} carries environment_vars into the
    # command: leave it out and every variable below is silently dropped (the tool pins and FLAVOR never arrived).
    execute_command = "{{ .Vars }} sudo -E bash '{{ .Path }}'"
    script          = "${path.root}/../scripts/linux/gates.sh"
    environment_vars = [
      "GOSS_URL=${lookup(lookup(var.tool_urls, var.architecture, {}), "goss", "")}",
      "GOSS_SHA256=${lookup(lookup(var.tool_sha256, var.architecture, {}), "goss", "")}",
      "TRIVY_URL=${lookup(lookup(var.tool_urls, var.architecture, {}), "trivy", "")}",
      "TRIVY_SHA256=${lookup(lookup(var.tool_sha256, var.architecture, {}), "trivy", "")}",
      "SYFT_URL=${lookup(lookup(var.tool_urls, var.architecture, {}), "syft", "")}",
      "SYFT_SHA256=${lookup(lookup(var.tool_sha256, var.architecture, {}), "syft", "")}",
      "FLAVOR=${var.flavor}",
      "DISTRO_FAMILY=${var.distro_family}",
    ]
    # Test failures must not abort the build: the gates are evaluated from the evidence
    # afterwards (imagefactory gate-ingest) so a failing image is still recorded and traceable.
    valid_exit_codes = [0, 1]
  }

  provisioner "file" {
    direction   = "download"
    source      = "/tmp/imagefactory-reports/"
    destination = "${var.report_dir}/${var.image_version}/${source.type}/"
  }

  # Last: remove the tooling and evidence so none of it ships in the image.
  provisioner "shell" {
    execute_command = "sudo -E bash '{{ .Path }}'"
    script          = "${path.root}/../scripts/linux/seal.sh"
  }

  post-processor "manifest" {
    output     = var.manifest_path
    strip_path = true
    custom_data = {
      image_version = var.image_version
    }
  }
}
