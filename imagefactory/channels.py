"""Channels (dev -> canary -> stable): promotion rules, rollback and retention.

Principles from the plan: build once and promote the artifact (this module never builds);
every rule here is a blocking gate; rollback is repointing, never mutation.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Dict, List, Optional

from . import manifest as mf
from .config import CHANNELS, Config
from .platforms import Platform
from .signing import SigningError
from .store import Store
from .versioning import Version, business_days_between

Verifier = Callable[[mf.Manifest], None]


class PromotionError(Exception):
    pass


def channel_key(flavor: str, channel: str) -> str:
    return "channels/%s/%s.json" % (flavor, channel)


class ChannelService:
    def __init__(self, cfg: Config, store: Store, platforms: Dict[str, Platform],
                 verify_signature: Verifier, now: Callable[[], dt.datetime] = mf.utcnow):
        self.cfg, self.store, self.platforms = cfg, store, platforms
        self.verify_signature, self.now = verify_signature, now

    # --- state ------------------------------------------------------------------------
    def state(self, flavor: str, channel: str) -> Optional[Dict[str, Any]]:
        return self.store.get(channel_key(flavor, channel))

    def _project(self, m: mf.Manifest, channel: str) -> None:
        """Write the channel pointer to every enabled platform."""
        for name, plat in self.platforms.items():
            images = m.images.get(name)
            if not images:
                raise PromotionError("%s has no %s image" % (m.version, name))
            plat.set_channel(m.flavor, channel, Version.parse(m.version), images)

    def _set(self, m: mf.Manifest, channel: str, by: str, reason: str, previous: Optional[str]) -> Dict[str, Any]:
        old = self.state(m.flavor, channel) or {}
        if previous is None:
            # Re-promoting what the channel already serves must not make it its own N-1.
            previous = old.get("previous") if old.get("version") == m.version else old.get("version")
        st = {
            "flavor": m.flavor, "channel": channel, "version": m.version,
            "previous": previous,
            "promoted_at": mf.iso(self.now()), "promoted_by": by, "reason": reason,
            "history": ([{"version": old["version"], "until": mf.iso(self.now())}] if old.get("version") else [])
                       + old.get("history", []),
        }
        st["history"] = st["history"][:20]
        self._project(m, channel)          # platforms first: if projection fails, state is unchanged
        self.store.put(channel_key(m.flavor, channel), st)
        return st

    # --- promote ----------------------------------------------------------------------
    def promote(self, flavor: str, version: str, to: str, approved_by: str,
                expedite: bool = False, no_open_sev2: bool = False) -> Dict[str, Any]:
        if to not in CHANNELS:
            raise PromotionError("unknown channel %r" % to)
        m = mf.load(self.store, flavor, version)
        if m.deprecated_at:
            raise PromotionError("%s is deprecated" % version)

        if to != "dev":
            if approved_by.strip().lower() == m.author.strip().lower():
                raise PromotionError("promotion to %s needs an approver other than the author (%s)" % (to, m.author))
            self._check_gates(m, to)
            try:
                self.verify_signature(m)
            except SigningError as e:
                raise PromotionError("signature check failed: %s" % e)
            self._check_replication(m)
        else:
            self._check_gates(m, "dev")

        if to == "stable":
            self._check_stable(m, expedite, no_open_sev2)
        return self._set(m, to, approved_by, "promote", None)

    def _check_gates(self, m: mf.Manifest, channel: str) -> None:
        failing = m.failing_gates(self.cfg.required_gates(channel))
        if failing:
            raise PromotionError("gates not passing for %s: %s" % (channel, ", ".join(
                "%s (%s)" % (g, m.gates.get(g, {}).get("detail") or "missing") for g in failing)))

    def _check_replication(self, m: mf.Manifest) -> None:
        problems: List[str] = []
        for name, plat in self.platforms.items():
            problems += ["%s: %s" % (name, p) for p in
                         plat.verify_available(Version.parse(m.version), m.images.get(name, {}))]
        if problems:
            raise PromotionError("image not available everywhere: " + "; ".join(problems))

    def _check_stable(self, m: mf.Manifest, expedite: bool, no_open_sev2: bool) -> None:
        canary = self.state(m.flavor, "canary")
        if not canary or canary["version"] != m.version:
            raise PromotionError("%s must be in canary before stable (canary is %s)"
                                 % (m.version, (canary or {}).get("version")))
        need = self.cfg.bake_days("canary", expedite)
        have = business_days_between(mf.parse_iso(canary["promoted_at"]).date(), self.now().date())
        if have < need:
            raise PromotionError("canary bake incomplete: %d of %d business days%s"
                                 % (have, need, " (expedited)" if expedite else ""))
        if not no_open_sev2:
            raise PromotionError("confirm there is no open Sev-2 tied to %s (--no-open-sev2)" % m.version)

    # --- rollback ---------------------------------------------------------------------
    def rollback(self, flavor: str, channel: str, approved_by: str, reason: str) -> Dict[str, Any]:
        """Repoint the channel at the previous version. Running VMs are never touched."""
        cur = self.state(flavor, channel)
        if not cur or not cur.get("previous"):
            raise PromotionError("%s/%s has no previous version to roll back to" % (flavor, channel))
        target = mf.load(self.store, flavor, cur["previous"])
        if target.deprecated_at:
            raise PromotionError("previous version %s is deprecated" % target.version)
        self._check_replication(target)   # do not roll back onto an image that is gone
        # After rollback `previous` is the bad version, so a second rollback is an explicit roll-forward.
        return self._set(target, channel, approved_by, "rollback: " + reason, previous=cur["version"])

    # --- retention --------------------------------------------------------------------
    def _unreleased(self, flavor: str) -> List[str]:
        released = set(mf.list_versions(self.store, flavor))
        return [v for v in mf.used_versions(self.store, flavor) if v not in released]

    def _unreleased_images(self, flavor: str, version: str) -> Dict[str, mf.Manifest]:
        """platform -> build record that still holds images, for a version that never released."""
        out = {}
        for plat in mf.part_platforms(self.store, flavor, version):
            part = mf.load_part(self.store, flavor, version, plat)
            if part.images.get(plat):
                out[plat] = part
        return out

    def retention_plan(self, flavor: str, today: dt.date = None) -> Dict[str, List[str]]:
        """What to deprecate, delete, and clean up.

        Supported = whatever channels point at, plus the previous stable (N and N-1). Everything else is
        deprecated; deprecated images are deleted after the grace period. Builds that never released
        (a gate failed) have no manifest and no channel, so they are cleaned up separately and sooner.
        """
        today = today or self.now().date()
        keep = set()
        for ch in CHANNELS:
            st = self.state(flavor, ch)
            if st:
                keep.add(st["version"])
                if ch == "stable" and st.get("previous"):
                    keep.add(st["previous"])
        grace = int(self.cfg.raw["retention"]["deprecated_grace_days"])
        unreleased_grace = int(self.cfg.raw["retention"].get("unreleased_grace_days", 7))
        deprecate, delete, unreleased = [], [], []
        for v in sorted(mf.list_versions(self.store, flavor), key=Version.parse):
            m = mf.load(self.store, flavor, v)
            if v in keep:
                continue
            if not m.deprecated_at:
                deprecate.append(v)
            elif (today - mf.parse_iso(m.deprecated_at).date()).days >= grace and any(m.images.values()):
                delete.append(v)
        for v in sorted(self._unreleased(flavor), key=Version.parse):
            parts = self._unreleased_images(flavor, v)
            if parts and min((today - mf.parse_iso(p.created_at).date()).days for p in parts.values()) >= unreleased_grace:
                unreleased.append(v)
        return {"deprecate": deprecate, "delete": delete, "delete_unreleased": unreleased}

    def retention_apply(self, flavor: str) -> Dict[str, List[str]]:
        plan = self.retention_plan(flavor)
        for v in plan["deprecate"]:
            m = mf.load(self.store, flavor, v)
            m.deprecated_at = mf.iso(self.now())
            mf.save(self.store, m)
        for v in plan["delete"]:
            m = mf.load(self.store, flavor, v)
            for name, plat in self.platforms.items():
                if m.images.get(name):
                    plat.deregister(Version.parse(v), m.images[name])
                    m.images[name] = {}      # the manifest, Git tag and evidence stay
            mf.save(self.store, m)
        for v in plan["delete_unreleased"]:
            for name, part in self._unreleased_images(flavor, v).items():
                if name in self.platforms:
                    self.platforms[name].deregister(Version.parse(v), part.images[name])
                    part.images[name] = {}   # the build record and evidence stay
                    mf.save_part(self.store, part, name)
        return plan
