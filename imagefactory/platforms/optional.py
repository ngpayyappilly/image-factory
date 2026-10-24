"""Optional platforms with Packer variable mapping only: VMware.
(AWS, Azure and GCP have full runtime plugins: see aws.py, azure.py, gcp.py.)

Packer variable mapping is real and `packer validate`-able. Channel projection, availability
checks and retention are NOT implemented yet; they raise PlatformNotImplemented rather than
silently doing nothing, so enabling one of these in config fails loudly at promotion time.
"""
from __future__ import annotations

from .base import UnimplementedRuntime


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
