"""Platform plugin interface. Everything cloud-specific lives behind this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from ..versioning import Version


class PlatformNotImplemented(NotImplementedError):
    pass


class Platform(ABC):
    name = ""
    # Matches the Packer source type, used for `packer build -only`.
    packer_source = ""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def packer_vars(self, flavor_cfg: Dict[str, Any], version: Version) -> Dict[str, Any]:
        """Map config/images.yaml onto this platform's Packer variables."""

    @abstractmethod
    def images_from_packer(self, artifact_id: str, flavor: str, version: Version) -> Dict[str, str]:
        """The {location: image reference} a finished build produced. `artifact_id` is Packer's manifest
        value; platforms whose references are deterministic (Azure, GCP) may derive them from config,
        flavor and version instead of parsing it."""

    @abstractmethod
    def verify_available(self, version: Version, images: Dict[str, str]) -> List[str]:
        """Return a list of problems; empty means the image is usable everywhere it should be."""

    @abstractmethod
    def set_channel(self, flavor: str, channel: str, version: Version, images: Dict[str, str]) -> None:
        """Point this platform's native channel pointer at the version."""

    @abstractmethod
    def deregister(self, version: Version, images: Dict[str, str]) -> None:
        """Remove the image from the platform (retention)."""

    def smoke_test(self, flavor: str, version: Version, images: Dict[str, str], flavor_cfg: Dict[str, Any]) -> List[str]:
        """Boot the image from the platform's own distribution (not the build VM) and check it comes up.
        Returns problems; empty means pass. Also the building block for the image-availability SLI."""
        raise PlatformNotImplemented("%s: smoke_test is not implemented yet" % self.name)


class UnimplementedRuntime(Platform):
    """Base for optional platforms: Packer variable mapping works, runtime ops do not yet."""

    def _no(self, op: str):
        raise PlatformNotImplemented(
            "%s: %s is not implemented yet. Build with Packer works (packer_vars), but channel "
            "projection/retention for this platform still needs a plugin (see docs/PLATFORMS.md)."
            % (self.name, op)
        )

    def images_from_packer(self, artifact_id, flavor, version):
        self._no("images_from_packer")

    def verify_available(self, version, images):
        self._no("verify_available")

    def set_channel(self, flavor, channel, version, images):
        self._no("set_channel")

    def deregister(self, version, images):
        self._no("deregister")

    def smoke_test(self, flavor, version, images, flavor_cfg):
        self._no("smoke_test")
