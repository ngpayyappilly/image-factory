"""The build manifest: what was built, from what, and which gates it has passed."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .store import Store
from .versioning import next_version


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def manifest_key(flavor: str, version: str) -> str:
    return "manifests/%s/%s.json" % (flavor, version)


@dataclass
class Manifest:
    version: str
    flavor: str
    baseline_version: str
    commit: str
    pipeline_run: str
    author: str
    created_at: str
    arch: str = "x86_64"
    # platform -> location -> image reference (AMI id, gallery version id, ...)
    images: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Aggregated view used by promotion: gate -> {"status": "pass"|"fail", "at": iso, "detail": str}.
    # A gate passes only when every platform the image was built for has passed it.
    gates: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Raw evidence results: gate -> platform ("*" = platform-independent) -> result
    gate_results: Dict[str, Dict[str, Dict[str, str]]] = field(default_factory=dict)
    sbom: Optional[str] = None
    signature: Optional[str] = None
    deprecated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Manifest":
        return cls(**d)

    def canonical_bytes(self) -> bytes:
        """Bytes that get signed: everything that identifies the image and its evidence.

        Gates, signature and deprecation are mutable lifecycle state, so they are excluded;
        what the signature binds is the image identity and provenance inputs.
        """
        d = self.to_dict()
        for k in ("gates", "gate_results", "signature", "deprecated_at"):
            d.pop(k)
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()

    def record_gate(self, name: str, status: str, detail: str = "", at: dt.datetime = None,
                    platform: str = None) -> None:
        assert status in ("pass", "fail")
        self.gate_results.setdefault(name, {})[platform or "*"] = {
            "status": status, "detail": detail, "at": iso(at or utcnow())}
        self.refresh_gates()

    def refresh_gates(self) -> None:
        """Recompute the aggregate gates from per-platform results (call after images change)."""
        self.gates = {}
        for name, by_plat in self.gate_results.items():
            failed = {p: r for p, r in by_plat.items() if r["status"] == "fail"}
            latest = max(r["at"] for r in by_plat.values())
            if failed:
                detail = "; ".join("%s: %s" % (p, r["detail"]) for p, r in sorted(failed.items()))
                self.gates[name] = {"status": "fail", "detail": detail, "at": latest}
            elif "*" in by_plat or set(self.images) <= set(by_plat):
                detail = "; ".join("%s: %s" % (p, r["detail"]) for p, r in sorted(by_plat.items()))
                self.gates[name] = {"status": "pass", "detail": detail, "at": latest}
            # else: a built platform has no evidence yet, so the gate stays unmet

    def failing_gates(self, required: List[str]) -> List[str]:
        return [g for g in required if self.gates.get(g, {}).get("status") != "pass"]


def load(store: Store, flavor: str, version: str) -> Manifest:
    d = store.get(manifest_key(flavor, version))
    if d is None:
        raise KeyError("no manifest for %s" % version)
    return Manifest.from_dict(d)


def save(store: Store, m: Manifest) -> None:
    store.put(manifest_key(m.flavor, m.version), m.to_dict())


def list_versions(store: Store, flavor: str) -> List[str]:
    """Released versions: the ones that have an assembled manifest."""
    prefix = "manifests/%s/" % flavor
    return [k[len(prefix):-len(".json")] for k in store.list(prefix) if k.endswith(".json")]


# --- per-platform build records -------------------------------------------------------------
# Each platform build writes ONLY its own record, `builds/<flavor>/<version>/<platform>.json`, so
# concurrent platform builds never touch the same object. `release` is the single writer of the
# assembled manifest. A record is a Manifest holding just that platform's images and gate results.

class IncompleteBuild(Exception):
    pass


def part_key(flavor: str, version: str, platform: str) -> str:
    return "builds/%s/%s/%s.json" % (flavor, version, platform)


def load_part(store: Store, flavor: str, version: str, platform: str) -> Manifest:
    d = store.get(part_key(flavor, version, platform))
    if d is None:
        raise KeyError("no build recorded for %s on %s" % (version, platform))
    return Manifest.from_dict(d)


def save_part(store: Store, m: Manifest, platform: str) -> None:
    store.put(part_key(m.flavor, m.version, platform), m.to_dict())


def part_platforms(store: Store, flavor: str, version: str) -> List[str]:
    prefix = "builds/%s/%s/" % (flavor, version)
    # names starting with "_" are bookkeeping (e.g. the version reservation), not platform builds
    return [k[len(prefix):-len(".json")] for k in store.list(prefix)
            if k.endswith(".json") and not k[len(prefix):].startswith("_")]


def reserve_version(store: Store, flavor: str, today: dt.date, owner: str,
                    now=utcnow) -> str:
    """Pick the next version AND claim it atomically (create-if-absent), so two overlapping runs can
    never be handed the same version, whatever the CI system does about concurrency."""
    for _ in range(50):
        version = str(next_version(flavor, today, used_versions(store, flavor)))
        if store.put_if_absent("builds/%s/%s/_reserved.json" % (flavor, version), {"owner": owner, "at": iso(now())}):
            return version
    raise RuntimeError("could not reserve a version for %s" % flavor)


def used_versions(store: Store, flavor: str) -> List[str]:
    """Every version ever started for the flavor, released or not. Used to pick the next build
    number: an unreleased build still owns its AMI name, so its number must not be reused."""
    seen = set(list_versions(store, flavor))
    prefix = "builds/%s/" % flavor
    for k in store.list(prefix):
        seen.add(k[len(prefix):].split("/", 1)[0])
    return sorted(seen)


def assemble(store: Store, flavor: str, version: str, platforms: List[str]) -> Manifest:
    """Merge the per-platform records into one manifest. Raises IncompleteBuild if a platform
    that should have been built has no record, or the records disagree about what was built."""
    have = set(part_platforms(store, flavor, version))
    missing = [p for p in platforms if p not in have]
    if missing:
        raise IncompleteBuild("no build recorded for platform(s): %s" % ", ".join(missing))
    parts = sorted((load_part(store, flavor, version, p) for p in platforms), key=lambda m: (m.created_at, m.pipeline_run))
    base = parts[0]
    for other in parts[1:]:
        for f in ("version", "flavor", "arch", "commit", "baseline_version"):
            if getattr(other, f) != getattr(base, f):
                raise IncompleteBuild("platform builds disagree on %s: %r vs %r" % (f, getattr(base, f), getattr(other, f)))
    m = Manifest(version=base.version, flavor=base.flavor, baseline_version=base.baseline_version, commit=base.commit,
                 pipeline_run=base.pipeline_run, author=base.author, created_at=base.created_at, arch=base.arch)
    for part in parts:
        m.images.update(part.images)
        for gate, by_platform in part.gate_results.items():
            m.gate_results.setdefault(gate, {}).update(by_platform)
    m.refresh_gates()
    return m
