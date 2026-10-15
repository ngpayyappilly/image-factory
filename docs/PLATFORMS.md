# Platform status

| Platform | Packer build | Channels / availability / smoke / retention | Consumer Terraform module |
|---|---|---|---|
| **AWS** (primary) | ✅ Linux (`al2023`, `rhel9`), Windows scaffold | ✅ SSM pointers, per-region checks, boot smoke test, AMI + snapshot retention | ✅ `golden-image-aws` |
| Azure (optional) | ✅ validates (Compute Gallery destination) | ✅ gallery version checks, channel tags, boot smoke test, version retention (via `az`) | ✅ `golden-image-azure` + `azure-bootstrap` |
| GCP (optional) | ✅ validates (Linux only) | ❌ | ❌ |
| VMware (optional) | ✅ validates (Linux, `vsphere-clone` to a Content Library) | ❌ | ❌ |

Enabling an optional platform: set `platforms.<name>.enabled: true` in `config/images.yaml` and fill its block. For **GCP and VMware**
builds will then work, but **promotion will fail loudly** until their plugins are written; that is intentional so an image never reaches
a channel that nothing actually serves.

## Azure
Implemented in `imagefactory/platforms/azure.py`, entirely through the `az` CLI (no Python SDK), so it is unit-tested with a fake
runner.

| Concern | How |
|---|---|
| Images | Compute Gallery **image versions** under one image definition per flavor, named `<flavor>`; Packer `azure-arm` publishes them |
| Version name | `Version.azure`, `YYYY.<MMDD as an integer>.<build>`: `rhel9-20261112.1` is `2026.1112.1`, Jan 5 is `2026.105.1` (no leading zero, so it matches what Azure stores) |
| Channel pointers | **Tags on the image definition**: `imagefactory-<channel>` (Azure version, what Terraform resolves) and `imagefactory-<channel>-version` (canonical). Written with one `az tag update --operation Merge`, so an update is atomic |
| Availability check | Version exists, `provisioningState` is `Succeeded`, replication `Completed`, every configured region is a target region |
| Boot smoke test | Create a VM from the gallery version in each replication region (no public IP, no NSG, disks and NIC deleted with the VM), wait for the VM agent to report `Ready`, always delete it |
| Retention | `az sig image-version delete` (idempotent) |
| References in the manifest | `region -> gallery image version resource ID`, derived from config and version rather than parsed from Packer's artifact id |

**Setting it up**
1. `cd terraform/azure-bootstrap && terraform apply -var subscription_id=… -var github_repository=<org>/<repo>`. Names, regions and which flavors
   get an image definition are read from `config/images.yaml`, so they cannot drift. It creates the gallery, one image definition per Azure flavor,
   build and smoke-test resource groups, a private smoke-test network, and two **managed identities with GitHub OIDC** (builder: runs on `main`;
   promoter: only inside the approval environments).
2. Put `terraform output smoke_test_subnet_ids` in `platforms.azure.smoke_test.subnet_ids`, fill `subscription_id`, set `enabled: true`.
3. Set the repository variables from `terraform output github_actions_variables` (`AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`,
   `AZURE_BUILDER_CLIENT_ID`, `AZURE_PROMOTER_CLIENT_ID`). The workflows sign in with `azure/login` only when `AZURE_TENANT_ID` is set.
4. Consumers: `module "image" { source = ".../golden-image-azure", flavor = "rhel9", gallery_name = …, gallery_resource_group_name = … }`, then
   `source_image_id = module.image.image_id` on the VM. It follows `stable` by default; pinning is an explicit, warned variable. Grant consumers the
   `imagefactory-consumer` role (`terraform output consumer_role_definition_id`).

State (manifests, locks, channel history) still lives in S3, so an Azure-enabled run uses **both** clouds: AWS credentials for the store and Azure credentials
for the gallery. Azure DevOps agents must already be signed in to `az`; only the GitHub workflows do it for you.

**Not verified:** nothing has run against a real Azure subscription. The Terraform validates against the real provider schema, and every `az` flag the plugin uses was checked
against the Azure CLI's own source (`sig image-version`, `vm create`, `tag update`), but none of it has been executed. Expect first-run fixes (role scopes, quota, the
VM agent status).

## Writing a platform plugin
Implement `imagefactory/platforms/base.py::Platform`: `images_from_packer`, `verify_available`, `set_channel`, `deregister`,
`smoke_test` (and keep the existing `packer_vars`). Register it in `platforms/__init__.py`. Channel state itself is platform-neutral
(S3), so a plugin only *projects* it (Azure uses tags on the gallery image definition; vSphere could repoint a Content Library item). Azure is
the second complete example after AWS: copy its shape (injectable CLI runner, deterministic image references, fakes in the tests).

## CPU architecture
| Architecture | AWS | Azure / GCP / VMware |
|---|---|---|
| x86_64 | ✅ `al2023`, `rhel9`, `win2022` | ✅ (Packer builds) |
| arm64 (Graviton) | ✅ `al2023arm` (Linux only) | ❌ rejected at config load |

Architecture is per flavor; see README "CPU architectures". Adding arm64 elsewhere means arm source images, instance/VM sizes and
tool pins for that platform, then relaxing the check in `Config.validate`.

## Version string differences
The canonical version (`rhel9-20261112.1`) is stored as a tag/label on every platform. Where the platform can't hold it as a name:
Azure Compute Gallery uses `2026.1112.1` (integer segments, so January 5 is `2026.105.1`), GCE image names use `rhel9-20261112-1` (see `Version.azure` / `Version.gcp`).

## Windows (scaffold)
`packer/windows` + `packer/scripts/windows` + `tests/pester` mirror the Linux contract (same evidence files, same gates) for AWS and Azure.
It has not been executed. Still to do before relying on it: DSC-based CIS Level 1, KMS/AVMA activation and domain-join smoke tests
(these run on a launched instance, not in the build VM), vSphere builder. The plan keeps Windows one step behind Linux.
