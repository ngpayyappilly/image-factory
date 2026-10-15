"""Per-flavor release, per-platform build records, and the cleanup of builds that never released."""
import datetime as dt
import json
import threading

import pytest

from imagefactory import cli
from imagefactory import manifest as mf
from imagefactory import release as rel
from imagefactory.manifest import IncompleteBuild

PRE = ["goss", "compliance", "vuln_scan", "secrets", "sbom"]
OK_SMOKE = lambda cfg, store, flavor, version: True
SIGN = lambda m, key: "sig"


def record_build(env, flavor, version, platform="aws", failing=(), skip=(), created=None, commit="abc"):
    """What a build job leaves behind: its own build record with images and gate results."""
    part = mf.Manifest(version=version, flavor=flavor, baseline_version="1.0.0", commit=commit, pipeline_run="1",
                       author="alice", created_at=created or mf.iso(env.clock()),
                       images={platform: {"us-east-1": "ami-" + version}})
    for g in PRE:
        if g not in skip:
            part.record_gate(g, "fail" if g in failing else "pass", "detail-" + g, platform=platform)
    mf.save_part(env.store, part, platform)
    return part


def release(env, flavor, version, **kw):
    kw.setdefault("smoke", OK_SMOKE)
    kw.setdefault("sign", SIGN)
    return rel.release_flavor(env.cfg, env.store, env.svc, flavor, version, "key", "run-1", **kw)


# --- one flavor must never block another --------------------------------------------------

def test_a_failing_flavor_does_not_block_the_passing_one(env):
    good, bad = "al2023-20261112.1", "rhel9-20261112.1"
    record_build(env, "al2023", good)
    record_build(env, "rhel9", bad, failing=["vuln_scan"])
    a, b = release(env, "al2023", good), release(env, "rhel9", bad)
    assert a.status == rel.RELEASED and b.status == rel.SKIPPED and "vuln_scan" in b.detail
    assert mf.load(env.store, "al2023", good).signature == "sig"
    assert env.plat.channels[("al2023", "dev")] == good
    assert env.store.get(mf.manifest_key("rhel9", bad)) is None          # nothing signed or published for the bad one
    assert ("rhel9", "dev") not in env.plat.channels


def test_cli_releases_ready_flavors_but_exits_nonzero_if_any_was_skipped(env, monkeypatch, capsys, tmp_path):
    import yaml
    raw = yaml.safe_load(open("config/images.yaml"))
    raw["store"] = {"type": "local", "local_path": str(tmp_path)}
    (tmp_path / "images.yaml").write_text(yaml.safe_dump(raw))
    record_build(env, "al2023", "al2023-20261112.1")
    record_build(env, "rhel9", "rhel9-20261112.1", skip=["sbom"])          # sbom evidence never arrived
    monkeypatch.setattr(rel, "run_smoke", OK_SMOKE)
    monkeypatch.setattr("imagefactory.signing.sign", SIGN)
    monkeypatch.setattr("imagefactory.cli._service", lambda cfg, flavor: env.svc)
    matrix = json.dumps({"a": {"flavor": "al2023", "platform": "aws", "version": "al2023-20261112.1"},
                         "b": {"flavor": "rhel9", "platform": "aws", "version": "rhel9-20261112.1"}})
    monkeypatch.setattr(cli, "store_from_config", lambda cfg: env.store)
    assert cli.main(["--config", str(tmp_path / "images.yaml"), "release", "--matrix", matrix, "--run-id", "5"]) == 1
    out = capsys.readouterr()
    assert "RELEASED" in out.out and "SKIPPED" in out.out and "sbom (missing)" in out.out
    assert "1 of 2 flavor(s) not released: rhel9" in out.err
    assert env.plat.channels[("al2023", "dev")] == "al2023-20261112.1"


def test_missing_build_record_means_not_released(env):
    o = release(env, "al2023", "al2023-20261112.1")        # the build job crashed before recording anything
    assert o.status == rel.SKIPPED and "no build recorded" in o.detail


def test_boot_smoke_failure_stops_before_signing(env):
    record_build(env, "al2023", "al2023-20261112.1")
    o = release(env, "al2023", "al2023-20261112.1", smoke=lambda *a: False)
    assert o.status == rel.FAILED and "smoke" in o.detail
    assert not mf.load(env.store, "al2023", "al2023-20261112.1").signature
    assert ("al2023", "dev") not in env.plat.channels


def test_release_is_idempotent_and_retryable(env):
    v = "al2023-20261112.1"
    record_build(env, "al2023", v)
    assert release(env, "al2023", v, smoke=lambda *a: False).status == rel.FAILED      # first attempt: smoke fails
    assert release(env, "al2023", v).status == rel.RELEASED                              # retry re-assembles and succeeds
    assert release(env, "al2023", v).status == rel.ALREADY                               # a re-run of the job is a no-op
    assert mf.load(env.store, "al2023", v).signature == "sig"


# --- per-platform build records ----------------------------------------------------------

def test_platform_builds_write_separate_records_and_assemble_into_one_manifest(env):
    v = "rhel9-20261112.1"
    record_build(env, "rhel9", v, platform="aws")
    record_build(env, "rhel9", v, platform="azure")
    assert sorted(mf.part_platforms(env.store, "rhel9", v)) == ["aws", "azure"]
    m = mf.assemble(env.store, "rhel9", v, ["aws", "azure"])
    assert set(m.images) == {"aws", "azure"} and m.failing_gates(PRE) == []
    assert set(m.gate_results["goss"]) == {"aws", "azure"}


def test_assemble_requires_every_platform_and_consistent_builds(env):
    v = "rhel9-20261112.1"
    record_build(env, "rhel9", v, platform="aws")
    with pytest.raises(IncompleteBuild, match="azure"):
        mf.assemble(env.store, "rhel9", v, ["aws", "azure"])
    record_build(env, "rhel9", v, platform="azure", commit="DIFFERENT")
    with pytest.raises(IncompleteBuild, match="disagree on commit"):
        mf.assemble(env.store, "rhel9", v, ["aws", "azure"])


def test_one_platform_failing_a_gate_blocks_the_flavor(env):
    v = "rhel9-20261112.1"
    record_build(env, "rhel9", v, platform="aws")
    record_build(env, "rhel9", v, platform="azure", failing=["goss"])
    m = mf.assemble(env.store, "rhel9", v, ["aws", "azure"])
    assert m.failing_gates(PRE) == ["goss"] and "azure" in m.gates["goss"]["detail"]


def test_concurrent_platform_builds_never_lose_an_update(env):
    """The old design had every platform job read-modify-write one manifest. Now each owns its record."""
    v = "rhel9-20261112.1"
    platforms = ["aws", "azure", "gcp", "vmware"]
    threads = [threading.Thread(target=record_build, args=(env, "rhel9", v, p)) for p in platforms]
    [t.start() for t in threads]
    [t.join() for t in threads]
    m = mf.assemble(env.store, "rhel9", v, platforms)
    assert set(m.images) == set(platforms)
    assert all(set(by_plat) == set(platforms) for by_plat in m.gate_results.values())


def test_unreleased_builds_still_own_their_version_number(env):
    record_build(env, "al2023", "al2023-20261112.1")                # built, never released
    env.make("al2023-20261112.2")                                    # released
    assert mf.used_versions(env.store, "al2023") == ["al2023-20261112.1", "al2023-20261112.2"]
    assert mf.list_versions(env.store, "al2023") == ["al2023-20261112.2"]


# --- cleanup of builds that never released ------------------------------------------------

def test_unreleased_builds_are_cleaned_up_after_the_grace_period_only(env):
    old = "al2023-20261101.1"
    record_build(env, "al2023", old, created=mf.iso(env.clock()))
    assert env.svc.retention_plan("al2023")["delete_unreleased"] == []          # still in the grace window
    env.clock.advance(7)
    assert env.svc.retention_plan("al2023")["delete_unreleased"] == [old]
    env.svc.retention_apply("al2023")
    assert env.plat.deregistered == [old]
    assert mf.load_part(env.store, "al2023", old, "aws").images == {"aws": {}}   # record + evidence stay
    assert env.svc.retention_plan("al2023")["delete_unreleased"] == []          # idempotent
    env.svc.retention_apply("al2023")
    assert env.plat.deregistered == [old]                                        # nothing deleted twice


def test_overlapping_plans_are_handed_distinct_versions(env):
    """Two CI runs planning at the same moment must not both get .1 (they would collide on the AMI name)."""
    got = []
    ts = [threading.Thread(target=lambda i=i: got.append(mf.reserve_version(env.store, "al2023", dt.date(2026, 11, 12), "run-%d" % i)))
          for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(got) == ["al2023-20261112.%d" % n for n in range(1, 9)]
    assert mf.part_platforms(env.store, "al2023", got[0]) == []             # a reservation is not a platform build
