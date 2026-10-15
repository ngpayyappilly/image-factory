"""Loads config/images.yaml, the single source of truth for flavors, platforms and policy."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "images.yaml"
CHANNELS = ("dev", "canary", "stable")
ARCHITECTURES = ("x86_64", "arm64")   # AWS naming; arm64 = Graviton


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, raw: Dict[str, Any], exceptions: List[Dict[str, Any]] = None):
        self.raw = raw
        self.exceptions = exceptions or []

    @classmethod
    def load(cls, path: Path = None) -> "Config":
        path = Path(path or DEFAULT_CONFIG)
        raw = yaml.safe_load(path.read_text())
        exc_path = path.parent / "exceptions.yaml"
        exceptions = []
        if exc_path.exists():
            exceptions = (yaml.safe_load(exc_path.read_text()) or {}).get("exceptions") or []
        cfg = cls(raw, exceptions)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        for flavor, img in self.raw["images"].items():
            arch = img.get("arch", "x86_64")
            if arch not in ARCHITECTURES:
                raise ConfigError("image %s: arch %r is not one of %s" % (flavor, arch, ", ".join(ARCHITECTURES)))
            if arch != "x86_64":
                # Everything else (Azure/GCP/vSphere sources, Windows) is only wired for x64.
                if img["os_family"] != "linux":
                    raise ConfigError("image %s: %s is supported for Linux flavors only" % (flavor, arch))
                other = [p for p in img["platforms"] if p != "aws"]
                if other:
                    raise ConfigError("image %s: %s is supported on aws only, remove %s" % (flavor, arch, ", ".join(other)))
            for p in img["platforms"]:
                if p not in self.raw["platforms"]:
                    raise ConfigError("image %s targets unknown platform %s" % (flavor, p))
                if p not in img:
                    raise ConfigError("image %s is missing a %s: block" % (flavor, p))
        for e in self.exceptions:
            for k in ("id", "gate", "reason", "approved_by", "expires"):
                if k not in e:
                    raise ConfigError("exception %r is missing %r (exceptions must be time-boxed and approved)" % (e, k))

    # --- accessors -------------------------------------------------------------------
    @property
    def baseline_version(self) -> str:
        return str(self.raw["baseline_version"])

    def image(self, flavor: str) -> Dict[str, Any]:
        try:
            return self.raw["images"][flavor]
        except KeyError:
            raise ConfigError("unknown flavor %r (known: %s)" % (flavor, ", ".join(self.raw["images"])))

    @property
    def schedule(self) -> Dict[str, Any]:
        return self.raw.get("schedule") or {}

    def arch(self, flavor: str) -> str:
        return self.image(flavor).get("arch", "x86_64")

    def flavors(self) -> List[str]:
        return list(self.raw["images"])

    def platform_cfg(self, name: str) -> Dict[str, Any]:
        return self.raw["platforms"][name]

    def enabled_platforms(self, flavor: str) -> List[str]:
        """Platforms the flavor supports AND that are switched on globally."""
        return [p for p in self.image(flavor)["platforms"] if self.platform_cfg(p).get("enabled")]

    @property
    def policy(self) -> Dict[str, Any]:
        return self.raw["promotion"]

    def required_gates(self, channel: str) -> List[str]:
        return list(self.policy["required_gates"][channel])

    def bake_days(self, channel: str, expedited: bool) -> int:
        if expedited:
            return int(self.policy["expedited_bake_business_days"])
        return int(self.policy["bake_business_days"].get(channel, 0))

    def active_exceptions(self, gate: str, today: dt.date = None) -> set:
        """IDs excepted for `gate` whose time-box has not expired."""
        today = today or dt.date.today()
        out = set()
        for e in self.exceptions:
            expires = e["expires"]
            if isinstance(expires, str):
                expires = dt.date.fromisoformat(expires)
            if e["gate"] == gate and expires >= today:
                out.add(e["id"])
        return out
