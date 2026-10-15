"""Optional platforms with Packer variable mapping only: GCP, VMware.
(Azure has a full runtime plugin: see azure.py.)

Packer variable mapping is real and `packer validate`-able. Channel projection, availability
checks and retention are NOT implemented yet; they raise PlatformNotImplemented rather than
silently doing nothing, so enabling one of these in config fails loudly at promotion time.
"""
from __future__ import annotations

from .base import UnimplementedRuntime


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
