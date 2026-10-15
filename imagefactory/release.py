"""Release: turn finished platform builds of ONE flavor into a signed manifest on the dev channel.

Each flavor is judged on its own. A failing flavor is reported and skipped; it never blocks the others.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

from . import manifest as mf
from . import signing
from .channels import ChannelService, PromotionError
from .config import Config
from .platforms import PlatformNotImplemented, enabled_platforms
from .store import Store
from .versioning import Version

RELEASED, ALREADY, SKIPPED, FAILED = "released", "already-released", "skipped", "failed"


@dataclass
class Outcome:
    flavor: str
    version: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (RELEASED, ALREADY)


def pre_release_gates(cfg: Config) -> List[str]:
    """Gates that must already be green when release starts: everything required for canary/stable
    except `smoke`, which release itself runs."""
    out: List[str] = []
    for ch in ("canary", "stable"):
        out += [g for g in cfg.required_gates(ch) if g != "smoke" and g not in out]
    return out


def run_smoke(cfg: Config, store: Store, flavor: str, version: str) -> bool:
    """Boot-test the image on every built platform and record the `smoke` gate."""
    m = mf.load(store, flavor, version)
    ok = True
    for name, plat in enabled_platforms(cfg, flavor).items():
        problems = plat.smoke_test(flavor, Version.parse(version), m.images.get(name, {}), cfg.image(flavor))
        m.record_gate("smoke", "fail" if problems else "pass",
                      "; ".join(problems) or "booted in %d location(s)" % len(m.images.get(name, {})), platform=name)
        print("smoke %s/%s: %s" % (version, name, "FAIL " + "; ".join(problems) if problems else "pass"))
        ok = ok and not problems
    mf.save(store, m)
    return ok


def release_flavor(cfg: Config, store: Store, svc: ChannelService, flavor: str, version: str, key_ref: str,
                   run_id: str, sign: Callable = None, smoke: Callable = None) -> Outcome:
    sign, smoke = sign or signing.sign, smoke or run_smoke       # resolved at call time so they can be substituted
    existing = store.get(mf.manifest_key(flavor, version))
    if existing and existing.get("signature"):
        return Outcome(flavor, version, ALREADY, "already signed and released")

    # 1. All platform builds present, consistent, and green (this is what a failed build job leaves unmet).
    try:
        m = mf.assemble(store, flavor, version, cfg.enabled_platforms(flavor))
    except mf.IncompleteBuild as e:
        return Outcome(flavor, version, SKIPPED, str(e))
    failing = m.failing_gates(pre_release_gates(cfg))
    if failing:
        return Outcome(flavor, version, SKIPPED, "gates not passing: " + ", ".join(
            "%s (%s)" % (g, m.gates.get(g, {}).get("detail") or "missing") for g in failing))

    # 2. Boot test, 3. sign once, 4. publish to dev.
    try:
        mf.save(store, m)
        if not smoke(cfg, store, flavor, version):
            return Outcome(flavor, version, FAILED, "boot smoke test failed")
        m = mf.load(store, flavor, version)
        m.signature = sign(m, key_ref)
        mf.save(store, m)
        svc.promote(flavor, version, "dev", "ci:%s" % run_id)
    except (PromotionError, signing.SigningError, PlatformNotImplemented) as e:
        return Outcome(flavor, version, FAILED, str(e))
    return Outcome(flavor, version, RELEASED, "signed and published to dev")
