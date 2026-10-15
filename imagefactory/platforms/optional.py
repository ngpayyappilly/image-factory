"""Optional platforms: Azure, GCP, VMware.

Packer variable mapping is real and `packer validate`-able. Channel projection, availability
checks and retention are NOT implemented yet; they raise PlatformNotImplemented rather than
silently doing nothing, so enabling one of these in config fails loudly at promotion time.
"""
from __future__ import annotations

from .base import UnimplementedRuntime


class AzurePlatform(UnimplementedRuntime):
    name = "azure"
    packer_source = "azure-arm"

    def packer_vars(self, flavor_cfg, version):
        a, c = flavor_cfg["azure"], self.config
        return {
            "azure_subscription_id": c["subscription_id"],
            "azure_build_resource_group": c["build_resource_group"],
            "azure_gallery_resource_group": c["gallery_resource_group"],
            "azure_gallery_name": c["gallery_name"],
            "azure_replication_regions": c["replication_regions"],
            "azure_image_publisher": a["publisher"],
            "azure_image_offer": a["offer"],
            "azure_image_sku": a["sku"],
            "azure_vm_size": a.get("vm_size", "Standard_D2s_v5"),
            "azure_image_version": version.azure,
        }


class GcpPlatform(UnimplementedRuntime):
    name = "gcp"
    packer_source = "googlecompute"

    def packer_vars(self, flavor_cfg, version):
        g, c = flavor_cfg["gcp"], self.config
        return {
            "gcp_project_id": c["project_id"],
            "gcp_zone": c["zone"],
            "gcp_image_storage_locations": c.get("image_storage_locations", []),
            "gcp_source_image_family": g["source_image_family"],
            "gcp_source_image_project": g["source_image_project"],
            "gcp_machine_type": g.get("machine_type", "e2-medium"),
            "gcp_image_name": version.gcp,
        }


class VmwarePlatform(UnimplementedRuntime):
    name = "vmware"
    packer_source = "vsphere-clone"

    def packer_vars(self, flavor_cfg, version):
        v, c = flavor_cfg["vmware"], self.config
        return {
            "vsphere_server": c["vcenter"],
            "vsphere_datacenter": c["datacenter"],
            "vsphere_cluster": c["cluster"],
            "vsphere_datastore": c["datastore"],
            "vsphere_folder": c.get("build_folder", ""),
            "vsphere_content_library": c["content_library"],
            "vsphere_template": v["template"],
            "vsphere_ssh_user": v.get("ssh_user", "root"),
        }
