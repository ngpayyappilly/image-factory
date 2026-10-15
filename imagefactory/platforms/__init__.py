from __future__ import annotations

from typing import Dict

from .aws import AwsPlatform
from .base import Platform, PlatformNotImplemented
from .azure import AzurePlatform
from .gcp import GcpPlatform
from .optional import VmwarePlatform

REGISTRY = {c.name: c for c in (AwsPlatform, AzurePlatform, GcpPlatform, VmwarePlatform)}


def get_platform(name: str, cfg) -> Platform:
    return REGISTRY[name](cfg.platform_cfg(name))


def enabled_platforms(cfg, flavor: str) -> Dict[str, Platform]:
    return {p: get_platform(p, cfg) for p in cfg.enabled_platforms(flavor)}


__all__ = ["Platform", "PlatformNotImplemented", "get_platform", "enabled_platforms", "REGISTRY"]
