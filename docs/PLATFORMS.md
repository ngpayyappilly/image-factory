# Platform status

| Platform | Packer build | Channels / availability / smoke / retention | Consumer Terraform module |
|---|---|---|---|
| **AWS** (primary) | ✅ Linux (`al2023`, `rhel9`), Windows scaffold | ✅ SSM pointers, per-region checks, boot smoke test, AMI + snapshot retention | ✅ `golden-image-aws` |
| Azure (optional) | ✅ validates (Compute Gallery destination) | ❌ raises `PlatformNotImplemented` | ❌ |
| GCP (optional) | ✅ validates (Linux only) | ❌ | ❌ |
| VMware (optional) | ✅ validates (Linux, `vsphere-clone` to a Content Library) | ❌ | ❌ |

Enabling an optional platform: set `platforms.<name>.enabled: true` in `config/images.yaml` and fill its block. Builds will then work,
but **promotion will fail loudly** until the plugin below is written; that is intentional so an image never reaches a channel that
nothing actually serves.

## Writing a platform plugin
Implement `imagefactory/platforms/base.py::Platform`: `images_from_packer`, `verify_available`, `set_channel`, `deregister`,
`smoke_test` (and keep the existing `packer_vars`). Register it in `platforms/__init__.py`. Channel state itself is platform-neutral
(S3), so a plugin only *projects* it: e.g. Azure could store the channel version in a gallery image tag or App Configuration and read it
from a Terraform module; vSphere could repoint a Content Library item.

## CPU architecture
| Architecture | AWS | Azure / GCP / VMware |
|---|---|---|
| x86_64 | ✅ `al2023`, `rhel9`, `win2022` | ✅ (Packer builds) |
| arm64 (Graviton) | ✅ `al2023arm` (Linux only) | ❌ rejected at config load |

Architecture is per flavor; see README "CPU architectures". Adding arm64 elsewhere means arm source images, instance/VM sizes and
tool pins for that platform, then relaxing the check in `Config.validate`.

## Version string differences
The canonical version (`rhel9-20261112.1`) is stored as a tag/label on every platform. Where the platform can't hold it as a name:
Azure Compute Gallery uses `2026.1112.1`, GCE image names use `rhel9-20261112-1` (see `Version.azure` / `Version.gcp`).

## Windows (scaffold)
`packer/windows` + `packer/scripts/windows` + `tests/pester` mirror the Linux contract (same evidence files, same gates) for AWS and Azure.
It has not been executed. Still to do before relying on it: DSC-based CIS Level 1, KMS/AVMA activation and domain-join smoke tests
(these run on a launched instance, not in the build VM), vSphere builder. The plan keeps Windows one step behind Linux.
