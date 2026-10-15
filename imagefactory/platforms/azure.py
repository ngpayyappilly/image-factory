"""Azure: Compute Gallery image versions; channels are tags on the gallery image definition.

All Azure access goes through the `az` CLI (already logged in by the CI job: `azure/login` OIDC on GitHub),
via an injectable runner, so there is no Python SDK dependency and the logic is unit-testable.

Layout
    gallery image definition   one per flavor, named <flavor>           (terraform/azure-bootstrap)
    gallery image version      <YYYY>.<MMDD>.<build>  (Version.azure)   built by Packer azure-arm
    channel pointers           tags on the image definition:
                                 imagefactory-<channel>          = Azure version  (what Terraform resolves)
                                 imagefactory-<channel>-version  = canonical ImageFactory version

`az tag update --operation Merge` changes only the named tags in one atomic call, so a channel update is not
a read-modify-write. `images` in the manifest map each replication region to the (single) image version
resource ID, mirroring AWS's region -> AMI.
"""
from __future__ import annotations

import json
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..versioning import Version
from .base import Platform

Runner = Callable[[List[str]], str]


class AzureCliError(Exception):
    pass


def _run(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        raise AzureCliError("the Azure CLI (az) was not found on PATH")
    except subprocess.CalledProcessError as e:
        raise AzureCliError("az %s failed: %s" % (" ".join(cmd[1:4]), (e.stderr or e.stdout).strip()))


def norm_region(r: str) -> str:
    """'East US 2' and 'eastus2' name the same region."""
    return r.lower().replace(" ", "")


def _not_found(e: AzureCliError) -> bool:
    return any(s in str(e) for s in ("ResourceNotFound", "NotFound", "was not found", "could not be found"))


class AzurePlatform(Platform):
    name = "azure"
    packer_source = "azure-arm"

    def __init__(self, config: Dict[str, Any], run: Runner = None):
        super().__init__(config)
        self._injected_run = run
        self._sleep = time.sleep

    # --- plumbing ---------------------------------------------------------------------
    def _az(self, *args: str) -> Any:
        out = (self._injected_run or _run)(["az", *args, "--only-show-errors", "-o", "json"])
        return json.loads(out) if out and out.strip() else None

    def definition_id(self, flavor: str) -> str:
        c = self.config
        return ("/subscriptions/%s/resourceGroups/%s/providers/Microsoft.Compute/galleries/%s/images/%s"
                % (c["subscription_id"], c["gallery_resource_group"], c["gallery_name"], flavor))

    def version_id(self, version: Version) -> str:
        return "%s/versions/%s" % (self.definition_id(version.flavor), version.azure)

    def _version_args(self, version: Version) -> List[str]:
        c = self.config
        return ["--resource-group", c["gallery_resource_group"], "--gallery-name", c["gallery_name"],
                "--gallery-image-definition", version.flavor, "--gallery-image-version", version.azure]

    # --- build ------------------------------------------------------------------------
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

    def images_from_packer(self, artifact_id, flavor, version):
        # The reference is fully determined by config + version, so we do not depend on the format of
        # Packer's artifact id. verify_available() proves the version really exists and has replicated.
        vid = self.version_id(version)
        return {region: vid for region in self.config["replication_regions"]}

    # --- distribution -----------------------------------------------------------------
    def verify_available(self, version, images):
        problems = []
        want = {norm_region(r) for r in self.config["replication_regions"]}
        problems += ["%s: no image replicated to this region" % r
                     for r in sorted(self.config["replication_regions"]) if r not in images]
        try:
            v = self._az("sig", "image-version", "show", *self._version_args(version), "--expand", "ReplicationStatus")
        except AzureCliError as e:
            if _not_found(e):
                return problems + ["%s not found in gallery %s" % (version.azure, self.config["gallery_name"])]
            raise
        if v.get("provisioningState") != "Succeeded":
            problems.append("%s is %s, not Succeeded" % (version.azure, v.get("provisioningState")))
        state = (v.get("replicationStatus") or {}).get("aggregatedState")
        if state != "Completed":
            problems.append("replication is %s, not Completed" % state)
        have = {norm_region(t.get("name", "")) for t in (v.get("publishingProfile") or {}).get("targetRegions", [])}
        problems += ["%s is not a target region of %s" % (r, version.azure) for r in sorted(want - have)]
        return problems

    # --- channels ---------------------------------------------------------------------
    def set_channel(self, flavor, channel, version, images):
        self._az("tag", "update", "--resource-id", self.definition_id(flavor), "--operation", "Merge", "--tags",
                 "imagefactory-%s=%s" % (channel, version.azure),
                 "imagefactory-%s-version=%s" % (channel, version))

    # --- retention --------------------------------------------------------------------
    def deregister(self, version, images):
        try:
            self._az("sig", "image-version", "delete", *self._version_args(version))
        except AzureCliError as e:
            if not _not_found(e):          # already gone is fine: retention must be idempotent
                raise

    # --- smoke test: boot-and-join from the gallery, in every replication region ---------
    def smoke_test(self, flavor, version, images, flavor_cfg):
        """Create a VM from the gallery image version, wait for the VM agent to report Ready (the Azure
        equivalent of passing status checks and registering), then delete it. Always cleans up."""
        cfg = self.config.get("smoke_test") or {}
        subnets, rg = cfg.get("subnet_ids") or {}, cfg.get("resource_group", "")
        if not rg:
            return ["platforms.azure.smoke_test.resource_group is not configured"]
        size = flavor_cfg["azure"].get("smoke_vm_size") or cfg.get("vm_size", "Standard_B2s")
        timeout = int(cfg.get("timeout_seconds", 900))
        problems = []
        for region in sorted(images):
            if region not in subnets:
                problems.append("%s: platforms.azure.smoke_test.subnet_ids has no subnet for this region" % region)
                continue
            problems += ["%s: %s" % (region, p) for p in self._smoke_one(
                region, images[region], version, size, rg, subnets[region], timeout, flavor_cfg["os_family"])]
        return problems

    def _auth_args(self, os_family: str, workdir: str) -> List[str]:
        if os_family == "windows":
            # Throwaway credential for a VM that lives for minutes and is never reachable from outside.
            return ["--admin-username", "ifadmin", "--admin-password", secrets.token_urlsafe(18) + "aA1!"]
        key = Path(workdir, "smoke_key")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        return ["--admin-username", "ifadmin", "--authentication-type", "ssh", "--ssh-key-values", str(key) + ".pub"]

    def _smoke_one(self, region, image_id, version, size, rg, subnet, timeout, os_family) -> List[str]:
        name = "ifsmoke" + secrets.token_hex(3)
        with tempfile.TemporaryDirectory() as workdir:
            try:
                create = ["vm", "create", "--resource-group", rg, "--name", name, "--location", region,
                          "--image", image_id, "--size", size, "--subnet", subnet,
                          "--public-ip-address", "", "--nsg", "",
                          "--os-disk-delete-option", "Delete", "--nic-delete-option", "Delete",
                          "--tags", "imagefactory-managed=true", "imagefactory-smoke-test=%s" % version,
                          *self._auth_args(os_family, workdir)]
                try:
                    self._az(*create)
                except AzureCliError as e:
                    return ["%s could not be created from the image: %s" % (name, e)]
                if not self._agent_ready(rg, name, timeout):
                    return ["%s booted but its VM agent never reported Ready within %ds" % (name, timeout)]
                return []
            finally:
                try:
                    self._az("vm", "delete", "--resource-group", rg, "--name", name, "--yes")
                except AzureCliError as e:
                    if not _not_found(e):
                        raise

    def _agent_ready(self, rg: str, name: str, timeout: int) -> bool:
        for _ in range(max(1, timeout // 15)):
            view = (self._az("vm", "get-instance-view", "--resource-group", rg, "--name", name) or {}).get("instanceView") or {}
            statuses = (view.get("vmAgent") or {}).get("statuses") or []
            if any(s.get("displayStatus") == "Ready" for s in statuses):
                return True
            self._sleep(15)
        return False
