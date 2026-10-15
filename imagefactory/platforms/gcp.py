"""GCP: Compute Engine custom images; channels are Secret Manager secrets.

All access goes through the `gcloud` CLI (already authenticated by the CI job: `google-github-actions/auth` with
Workload Identity Federation on GitHub), via an injectable runner, so there is no Python SDK dependency and the
logic is unit-testable.

Layout
    image                 <Version.gcp> e.g. rhel9-20261112-1, family <flavor>, built by Packer googlecompute
    channel pointers      secret  <prefix>-<flavor>-<channel>   (default prefix: imagefactory)
                          each promotion adds a version holding {"image": ..., "version": ..., "project": ...};
                          the latest enabled version is the pointer, older ones are the history

Why not image families for channels: an image has exactly one family, but the same version is routinely in two
channels at once (canary and stable right after a promotion), and a family resolves to the newest image, which
makes rolling back to an older one impossible without deprecating the newer ones.

GCE images are global, so the manifest maps each storage location (or "global") to the one image path.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..versioning import Version
from .base import Platform

Runner = Callable[[List[str]], str]
DEFAULT_BOOT_PATTERN = r"Started .*Guest Agent"


class GcloudError(Exception):
    pass


def _run(cmd: List[str]) -> str:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        raise GcloudError("the Google Cloud CLI (gcloud) was not found on PATH")
    except subprocess.CalledProcessError as e:
        raise GcloudError("gcloud %s failed: %s" % (" ".join(cmd[1:4]), (e.stderr or e.stdout).strip()))


def _not_found(e: GcloudError) -> bool:
    return any(s in str(e) for s in ("was not found", "NOT_FOUND", "notFound", "does not exist", "Not found"))


class GcpPlatform(Platform):
    name = "gcp"
    packer_source = "googlecompute"

    def __init__(self, config: Dict[str, Any], run: Runner = None):
        super().__init__(config)
        self._injected_run = run
        self._sleep = time.sleep
        self.project = config["project_id"]

    # --- plumbing ---------------------------------------------------------------------
    def _gcloud(self, *args: str, fmt: Optional[str] = "json") -> Any:
        cmd = ["gcloud", *args, "--project", self.project, "--quiet"]
        if fmt:
            cmd.append("--format=%s" % fmt)
        out = (self._injected_run or _run)(cmd)
        if fmt == "json":
            return json.loads(out) if out and out.strip() else None
        return out

    def secret_name(self, flavor: str, channel: str) -> str:
        return "%s-%s-%s" % (self.config.get("channel_secret_prefix", "imagefactory"), flavor, channel)

    def locations(self) -> List[str]:
        return list(self.config.get("image_storage_locations") or ["global"])

    # --- build ------------------------------------------------------------------------
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
            "gcp_subnetwork": c.get("build_subnetwork", ""),
            "gcp_use_internal_ip": bool(c.get("build_use_internal_ip", False)),
            "gcp_omit_external_ip": bool(c.get("build_omit_external_ip", False)),
            "gcp_build_service_account": c.get("build_service_account", ""),
        }

    def images_from_packer(self, artifact_id, flavor, version):
        # The image path is fully determined by project + name; verify_available() proves it exists.
        path = "projects/%s/global/images/%s" % (self.project, version.gcp)
        return {loc: path for loc in self.locations()}

    # --- distribution -----------------------------------------------------------------
    def verify_available(self, version, images):
        problems = ["%s: no image recorded for this storage location" % loc
                    for loc in sorted(self.locations()) if loc not in images]
        try:
            img = self._gcloud("compute", "images", "describe", version.gcp)
        except GcloudError as e:
            if _not_found(e):
                return problems + ["image %s not found in project %s" % (version.gcp, self.project)]
            raise
        if img.get("status") != "READY":
            problems.append("image %s is %s, not READY" % (version.gcp, img.get("status")))
        state = (img.get("deprecated") or {}).get("state", "ACTIVE")
        if state != "ACTIVE":
            problems.append("image %s is %s" % (version.gcp, state))
        label = (img.get("labels") or {}).get("imagefactory-version")
        if label != version.gcp:
            problems.append("image %s is labelled %r, expected %s" % (version.gcp, label, version.gcp))
        have = {s.lower() for s in img.get("storageLocations") or []}
        problems += ["image is not stored in %s" % loc for loc in sorted(self.locations())
                     if loc != "global" and loc.lower() not in have]
        return problems

    # --- channels ---------------------------------------------------------------------
    def set_channel(self, flavor, channel, version, images):
        """Add a version to the channel's secret. The secret itself is created by terraform/gcp-bootstrap (with the
        per-secret IAM that lets the builder write `dev` only)."""
        payload = {"image": version.gcp, "version": str(version), "project": self.project}
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "pointer.json")
            f.write_text(json.dumps(payload, sort_keys=True))
            self._gcloud("secrets", "versions", "add", self.secret_name(flavor, channel), "--data-file", str(f))

    # --- retention --------------------------------------------------------------------
    def deregister(self, version, images):
        try:
            self._gcloud("compute", "images", "delete", version.gcp)
        except GcloudError as e:
            if not _not_found(e):          # already gone is fine: retention must be idempotent
                raise

    # --- smoke test: boot the image and wait for the guest agent -----------------------------
    def smoke_test(self, flavor, version, images, flavor_cfg):
        """Create a VM from the image (no external IP), wait until the serial console shows the guest agent started
        (the GCE analogue of passing status checks and registering), then delete it. Always cleans up.
        GCE images are global, so this boots once, not once per location."""
        cfg = self.config.get("smoke_test") or {}
        if not cfg.get("subnetwork"):
            return ["platforms.gcp.smoke_test.subnetwork is not configured"]
        timeout = int(cfg.get("timeout_seconds", 600))
        pattern = re.compile(cfg.get("boot_pattern", DEFAULT_BOOT_PATTERN))
        zone, name = self.config["zone"], "ifsmoke-" + secrets.token_hex(3)
        try:
            try:
                self._gcloud("compute", "instances", "create", name, "--zone", zone,
                             "--image", version.gcp, "--image-project", self.project,
                             "--machine-type", cfg.get("machine_type", "e2-medium"),
                             "--subnet", cfg["subnetwork"], "--no-address",
                             "--labels", "imagefactory-managed=true,imagefactory-smoke-test=%s" % version.gcp)
            except GcloudError as e:
                return ["%s could not be created from the image: %s" % (name, e)]
            if not self._booted(name, zone, pattern, timeout):
                return ["%s started but the serial console never showed %r within %ds" % (name, pattern.pattern, timeout)]
            return []
        finally:
            try:
                self._gcloud("compute", "instances", "delete", name, "--zone", zone, "--delete-disks", "all")
            except GcloudError as e:
                if not _not_found(e):
                    raise

    def _booted(self, name: str, zone: str, pattern, timeout: int) -> bool:
        for _ in range(max(1, timeout // 15)):
            try:
                out = self._gcloud("compute", "instances", "get-serial-port-output", name, "--zone", zone, fmt=None)
            except GcloudError:
                out = ""                       # not available yet while the instance is still starting
            if pattern.search(out or ""):
                return True
            self._sleep(15)
        return False
