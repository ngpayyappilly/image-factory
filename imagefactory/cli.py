"""imagefactory CLI: thin glue between the pipeline (Packer/CI) and the platform-neutral core."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import gates as gates_mod
from . import lock as lock_mod
from . import manifest as mf
from . import release as release_mod
from . import schedule as schedule_mod
from . import signing
from .channels import ChannelService, PromotionError
from .config import CHANNELS, Config, ConfigError
from .platforms import PlatformNotImplemented, enabled_platforms, get_platform
from .store import S3Store, store_from_config
from .versioning import Version, is_build_day_for, next_version


def _service(cfg: Config, flavor: str) -> ChannelService:
    key = cfg.raw["signing"]["key_ref"]
    return ChannelService(cfg, store_from_config(cfg), enabled_platforms(cfg, flavor),
                          verify_signature=lambda m: signing.verify(m, key))


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


# --- commands ---------------------------------------------------------------------------
def cmd_version_next(a, cfg):
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    cfg.image(a.flavor)
    print(next_version(a.flavor, today, mf.used_versions(store_from_config(cfg), a.flavor)))


def cmd_schedule_check(a, cfg):
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    print("true" if is_build_day_for(today) else "false")


def cmd_packer_vars(a, cfg):
    v = Version.parse(a.version)
    img = cfg.image(a.flavor)
    plat = get_platform(a.platform, cfg)
    out = {
        "flavor": a.flavor, "image_version": str(v), "baseline_version": cfg.baseline_version,
        "git_commit": a.commit, "cis_role": img.get("cis_role", ""), "architecture": cfg.arch(a.flavor),
        "distro_family": cfg.distro_family(a.flavor), "cis_vars": cfg.cis_vars(a.flavor),
    }
    out.update(plat.packer_vars(img, v))
    _print(out)


def cmd_manifest_create(a, cfg):
    """Record ONE platform's build as its own object (`builds/<flavor>/<version>/<platform>.json`).

    Platform builds of the same flavor run concurrently; because each writes only its own record they can
    never overwrite each other. `release` assembles the single manifest afterwards.
    """
    store = store_from_config(cfg)
    plat = get_platform(a.platform, cfg)
    packer = json.loads(Path(a.packer_manifest).read_text())
    builds = [b for b in packer["builds"] if b["builder_type"] == plat.packer_source]
    if not builds:
        raise SystemExit("no %s build in %s" % (plat.packer_source, a.packer_manifest))
    images = plat.images_from_packer(builds[-1]["artifact_id"], a.flavor, Version.parse(a.version))
    released = store.get(mf.manifest_key(a.flavor, a.version))
    if released and released.get("signature"):
        raise SystemExit("%s is already released; images are immutable" % a.version)
    part = mf.Manifest(version=a.version, flavor=a.flavor, baseline_version=cfg.baseline_version,
                       commit=a.commit, pipeline_run=a.run_id, author=a.author, created_at=mf.iso(mf.utcnow()),
                       arch=cfg.arch(a.flavor), images={a.platform: images})
    if not store.put_if_absent(mf.part_key(a.flavor, a.version, a.platform), part.to_dict()):
        raise SystemExit("%s on %s is already recorded; a rebuild needs a new version" % (a.version, a.platform))
    print("recorded %s on %s -> %d location(s)" % (a.version, a.platform, len(images)))


def cmd_matrix(a, cfg):
    """Build matrix: one entry per (flavor, platform). The pipeline fans out over this."""
    rows = []
    for flavor in ([a.flavor] if a.flavor else cfg.flavors()):
        img = cfg.image(flavor)
        plats = img["platforms"] if a.all else cfg.enabled_platforms(flavor)
        for p in plats:
            if a.platform and p != a.platform:
                continue
            fam = img["os_family"]
            src = get_platform(p, cfg).packer_source
            rows.append({"flavor": flavor, "platform": p, "os_family": fam, "source_type": src, "arch": cfg.arch(flavor),
                         "packer_dir": "packer/%s" % fam, "only": "%s.%s.%s" % (fam, src, fam)})
    if a.format == "env":
        if len(rows) != 1:
            raise ConfigError("--format env needs exactly one (flavor, platform), got %d" % len(rows))
        for k, v in rows[0].items():
            print("%s=%s" % (k.upper(), v))
    else:
        _print(rows)


def cmd_plan(a, cfg):
    """One version per flavor, shared by every platform build of it, plus the build matrix.

    With --scheduled only flavors whose monthly build is *due* are planned (see schedule.py), so a failed
    or delayed run is retried by the next daily trigger. Flavors that ran out of attempts are reported.
    """
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    store = store_from_config(cfg)
    max_attempts = int(cfg.schedule.get("max_attempts_per_month", 3))
    matrix, exhausted = {}, []
    for flavor in (a.flavors.split(",") if a.flavors else cfg.flavors()):
        cfg.image(flavor)
        if a.scheduled:
            st = schedule_mod.status(store, flavor, today, max_attempts)
            print("%s: %s" % (flavor, st.describe()), file=sys.stderr)
            if st.state == schedule_mod.EXHAUSTED:
                exhausted.append(flavor)
            if st.state != schedule_mod.DUE:
                continue
        version = mf.reserve_version(store, flavor, today, a.run_id or os.environ.get("IMAGEFACTORY_RUN_ID", "local"))
        for p in cfg.enabled_platforms(flavor):
            matrix["%s_%s" % (flavor, p)] = {"flavor": flavor, "platform": p, "version": version}
    out = json.dumps(matrix, sort_keys=True)
    if a.ado_variable:   # Azure DevOps output variables
        print("##vso[task.setvariable variable=%s;isOutput=true]%s" % (a.ado_variable, out))
        print("##vso[task.setvariable variable=build;isOutput=true]%s" % ("true" if matrix else "false"))
        print("##vso[task.setvariable variable=exhausted;isOutput=true]%s" % ",".join(exhausted))
    if a.github_output:  # GitHub Actions: `matrix` for release, `include` for the job matrix
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("matrix=%s\ninclude=%s\nany=%s\nexhausted=%s\n" % (
                out, json.dumps({"include": list(matrix.values())}, sort_keys=True),
                "true" if matrix else "false", ",".join(exhausted)))
    print(out)


def cmd_evidence_upload(a, cfg):
    """Keep raw gate evidence next to the manifest (S3 lifecycle expires it; the manifest stays)."""
    store = store_from_config(cfg)
    if not isinstance(store, S3Store):
        print("evidence stays local (store is not S3)")
        return 0
    root, n = Path(a.reports), 0
    for p in sorted(root.rglob("*")):
        if p.is_file():
            store.put_file("evidence/%s/%s/%s" % (a.flavor, a.version, p.relative_to(root).as_posix()), p)
            n += 1
    print("uploaded %d evidence file(s) for %s" % (n, a.version))


def cmd_gate_ingest(a, cfg):
    """Evaluate this platform's evidence and store the results in this platform's build record."""
    store = store_from_config(cfg)
    part = mf.load_part(store, a.flavor, a.version, a.platform)
    results = gates_mod.ingest(Path(a.reports), cfg)
    for gate, (status, detail) in results.items():
        part.record_gate(gate, status, detail, platform=a.platform)
        print("%-11s %s  %s" % (gate, status.upper(), detail))
    mf.save_part(store, part, a.platform)
    # A gate with no evidence can never pass, so the image can never release. Say so now, in the build, instead of
    # letting the job succeed and the flavor be skipped later (a build with NO evidence used to exit 0).
    missing = [g for g in release_mod.pre_release_gates(cfg) if g not in results]
    for g in missing:
        print("%-11s MISSING  no evidence found under %s" % (g, a.reports))
    return 1 if missing or any(s == "fail" for s, _ in results.values()) else 0


def cmd_gate_record(a, cfg):
    """Record a gate result by hand on the released manifest."""
    store = store_from_config(cfg)
    m = mf.load(store, a.flavor, a.version)
    m.record_gate(a.gate, a.status, a.detail or "", platform=a.platform)
    mf.save(store, m)


def cmd_sign(a, cfg):
    store = store_from_config(cfg)
    m = mf.load(store, a.flavor, a.version)
    m.signature = signing.sign(m, cfg.raw["signing"]["key_ref"])
    mf.save(store, m)
    print("signed %s" % a.version)


def cmd_smoke(a, cfg):
    return 0 if release_mod.run_smoke(cfg, store_from_config(cfg), a.flavor, a.version) else 1


def cmd_release(a, cfg):
    """Release each flavor on its own merit; one failing flavor never blocks another.

    A flavor releases only if every platform build has been recorded and every pre-release gate is green. It
    is then boot-tested, signed once and published to dev. Exit status is non-zero if ANY flavor did not
    release, so the run is red, but the flavors that were ready are already out.
    """
    store = store_from_config(cfg)
    key_ref = cfg.raw["signing"]["key_ref"]
    outcomes = []
    for flavor, version in sorted({(e["flavor"], e["version"]) for e in json.loads(a.matrix).values()}):
        o = release_mod.release_flavor(cfg, store, _service(cfg, flavor), flavor, version, key_ref, a.run_id)
        outcomes.append(o)
        print("%-9s %-10s %s  %s" % (o.status.upper(), flavor, version, o.detail))
    not_released = [o for o in outcomes if not o.ok]
    if not_released:
        print("error: %d of %d flavor(s) not released: %s" % (len(not_released), len(outcomes),
              ", ".join(o.flavor for o in not_released)), file=sys.stderr)
        return 1
    return 0


def cmd_lock(a, cfg):
    store = store_from_config(cfg)
    if a.action == "acquire":
        lock_mod.acquire(store, a.name, a.owner, int(a.ttl_minutes * 60), int(a.wait_minutes * 60))
        print("lock %s acquired by %s" % (a.name, a.owner))
    else:
        print("lock %s %s" % (a.name, "released" if lock_mod.release(store, a.name, a.owner) else "was not held by this owner; nothing to release"))


def cmd_promote(a, cfg):
    st = _service(cfg, a.flavor).promote(a.flavor, a.version, a.to, a.approved_by,
                                         expedite=a.expedite, no_open_sev2=a.no_open_sev2)
    print("%s/%s -> %s" % (a.flavor, a.to, st["version"]))


def cmd_rollback(a, cfg):
    st = _service(cfg, a.flavor).rollback(a.flavor, a.channel, a.approved_by, a.reason)
    print("%s/%s rolled back to %s (was %s). Publish a notice; running VMs are unchanged."
          % (a.flavor, a.channel, st["version"], st["previous"]))


def cmd_status(a, cfg):
    store = store_from_config(cfg)
    for flavor in ([a.flavor] if a.flavor else cfg.flavors()):
        row = {ch: (store.get("channels/%s/%s.json" % (flavor, ch)) or {}).get("version", "-") for ch in CHANNELS}
        print("%-10s %s" % (flavor, "  ".join("%s=%s" % kv for kv in row.items())))


def cmd_retention(a, cfg):
    svc = _service(cfg, a.flavor)
    plan = svc.retention_apply(a.flavor) if a.apply else svc.retention_plan(a.flavor)
    print(("applied" if a.apply else "dry-run"))
    _print(plan)


# --- parser -----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="imagefactory")
    p.add_argument("--config", help="path to images.yaml (default: $IMAGEFACTORY_CONFIG, else config/images.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *args, **kw):
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(fn=fn)
        for spec in args:
            flags, opts = spec[0], spec[1]
            sp.add_argument(*flags, **opts)
        return sp

    F = (["--flavor"], {"required": True})
    V = (["--version"], {"required": True})
    add("version-next", cmd_version_next, F, (["--date"], {}))
    add("schedule-check", cmd_schedule_check, (["--today"], {}))
    add("matrix", cmd_matrix, (["--flavor"], {}), (["--platform"], {}),
        (["--all"], {"action": "store_true", "help": "include platforms that are switched off (for validation)"}),
        (["--format"], {"choices": ["json", "env"], "default": "json"}))
    add("plan", cmd_plan, (["--flavors"], {"help": "comma-separated; default all"}), (["--date"], {}),
        (["--run-id"], {"help": "recorded on the version reservation (default: $IMAGEFACTORY_RUN_ID)"}),
        (["--scheduled"], {"action": "store_true", "help": "only flavors whose monthly build is due (catches up after a failed or missed run)"}),
        (["--ado-variable"], {"help": "also emit an Azure DevOps output variable with this name"}),
        (["--github-output"], {"action": "store_true", "help": "also write matrix/include/any to $GITHUB_OUTPUT"}))
    add("evidence-upload", cmd_evidence_upload, F, V, (["--reports"], {"required": True}))
    add("packer-vars", cmd_packer_vars, F, V, (["--platform"], {"required": True}), (["--commit"], {"default": "unknown"}))
    add("manifest-create", cmd_manifest_create, F, V, (["--platform"], {"required": True}),
        (["--packer-manifest"], {"required": True}), (["--commit"], {"required": True}),
        (["--run-id"], {"required": True}), (["--author"], {"required": True}))
    PL = (["--platform"], {"help": "platform the evidence came from (omit for platform-independent)"})
    add("gate-ingest", cmd_gate_ingest, F, V, (["--reports"], {"required": True}), (["--platform"], {"required": True}))
    add("gate-record", cmd_gate_record, F, V, PL, (["--gate"], {"required": True}),
        (["--status"], {"required": True, "choices": ["pass", "fail"]}), (["--detail"], {}))
    add("sign", cmd_sign, F, V)
    add("smoke", cmd_smoke, F, V)
    add("lock", cmd_lock, (["action"], {"choices": ["acquire", "release"]}), (["--name"], {"required": True}),
        (["--owner"], {"required": True, "help": "who holds it, e.g. the pipeline run id"}),
        (["--ttl-minutes"], {"type": float, "default": 360, "help": "a killed run cannot block forever"}),
        (["--wait-minutes"], {"type": float, "default": 0, "help": "poll this long for a busy lock before failing"}))
    add("release", cmd_release, (["--matrix"], {"required": True, "help": "JSON from `plan`"}),
        (["--run-id"], {"default": "local"}))
    add("promote", cmd_promote, F, V, (["--to"], {"required": True, "choices": list(CHANNELS)}),
        (["--approved-by"], {"required": True}),
        (["--expedite"], {"action": "store_true", "help": "critical-CVE path: shortened canary bake"}),
        (["--no-open-sev2"], {"action": "store_true", "help": "confirm no open Sev-2 is tied to this image"}))
    add("rollback", cmd_rollback, F, (["--channel"], {"default": "stable", "choices": list(CHANNELS)}),
        (["--approved-by"], {"required": True}), (["--reason"], {"required": True}))
    add("status", cmd_status, (["--flavor"], {}))
    add("retention", cmd_retention, F, (["--apply"], {"action": "store_true"}))
    return p


def _cloud_errors() -> tuple:
    try:
        from botocore.exceptions import BotoCoreError, ClientError
        return (BotoCoreError, ClientError)
    except ImportError:
        return ()


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        cfg = Config.load(a.config)
        return a.fn(a, cfg) or 0
    except lock_mod.LockBusy as e:
        print("busy: %s" % e, file=sys.stderr)
        return 3
    except (PromotionError, ConfigError, signing.SigningError, PlatformNotImplemented, KeyError, ValueError,
            *_cloud_errors()) as e:
        print("error: %s" % (e.args[0] if isinstance(e, KeyError) else e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
