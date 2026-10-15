import pytest

from imagefactory import manifest as mf
from imagefactory.channels import PromotionError

V1, V2, V3 = "al2023-20261012.1", "al2023-20261112.1", "al2023-20261212.1"


def test_dev_needs_no_gates_or_approver(env):
    env.make(V1, gates=[], signed=False)
    env.svc.promote("al2023", V1, "dev", approved_by="ci")
    assert env.plat.channels[("al2023", "dev")] == V1


def test_canary_blocked_by_missing_or_failing_gate(env):
    env.make(V1, gates=["goss", "compliance", "secrets", "sbom", "smoke"])   # vuln_scan never recorded
    with pytest.raises(PromotionError, match="vuln_scan"):
        env.svc.promote("al2023", V1, "canary", approved_by="bob")
    m = mf.load(env.store, "al2023", V1)
    m.record_gate("vuln_scan", "fail", "CVE-1 critical")
    mf.save(env.store, m)
    with pytest.raises(PromotionError, match="CVE-1 critical"):
        env.svc.promote("al2023", V1, "canary", approved_by="bob")
    assert ("al2023", "canary") not in env.plat.channels


def test_author_cannot_approve_own_image(env):
    env.make(V1, author="Alice")
    with pytest.raises(PromotionError, match="other than the author"):
        env.svc.promote("al2023", V1, "canary", approved_by="alice")


def test_unsigned_or_badly_signed_is_blocked(env):
    env.make(V1, signed=False)
    with pytest.raises(PromotionError, match="signature"):
        env.svc.promote("al2023", V1, "canary", approved_by="bob")
    env.make(V2)
    env.signed_ok["value"] = False
    with pytest.raises(PromotionError, match="signature"):
        env.svc.promote("al2023", V2, "canary", approved_by="bob")


def test_replication_gap_blocks_promotion(env):
    env.make(V1)
    env.plat.problems = ["us-west-2: no image replicated to this region"]
    with pytest.raises(PromotionError, match="not available everywhere"):
        env.svc.promote("al2023", V1, "canary", approved_by="bob")


def test_stable_requires_canary_first(env):
    env.make(V1)
    with pytest.raises(PromotionError, match="must be in canary"):
        env.svc.promote("al2023", V1, "stable", approved_by="bob", no_open_sev2=True)


def test_stable_requires_bake_and_sev2_confirmation(env):
    env.make(V1)
    env.svc.promote("al2023", V1, "canary", approved_by="bob")     # Thu 12 Nov
    env.clock.advance(4)                                            # Mon 16 Nov: 2 business days
    with pytest.raises(PromotionError, match="bake incomplete: 2 of 5"):
        env.svc.promote("al2023", V1, "stable", approved_by="bob", no_open_sev2=True)
    env.clock.advance(3)                                            # Thu 19 Nov: 5 business days
    with pytest.raises(PromotionError, match="Sev-2"):
        env.svc.promote("al2023", V1, "stable", approved_by="bob")
    env.svc.promote("al2023", V1, "stable", approved_by="bob", no_open_sev2=True)
    assert env.plat.channels[("al2023", "stable")] == V1


def test_expedited_bake_is_shortened_not_skipped(env):
    env.make(V1)
    env.svc.promote("al2023", V1, "canary", approved_by="bob")
    with pytest.raises(PromotionError, match="0 of 1"):
        env.svc.promote("al2023", V1, "stable", approved_by="bob", expedite=True, no_open_sev2=True)
    env.clock.advance(1)
    env.svc.promote("al2023", V1, "stable", approved_by="bob", expedite=True, no_open_sev2=True)


def _stable(env, v, when_canary_days=7):
    env.make(v)
    env.svc.promote("al2023", v, "canary", approved_by="bob")
    env.clock.advance(when_canary_days)
    env.svc.promote("al2023", v, "stable", approved_by="bob", no_open_sev2=True)


def test_rollback_repoints_to_previous_without_rebuild(env):
    _stable(env, V1)
    _stable(env, V2)
    st = env.svc.state("al2023", "stable")
    assert (st["version"], st["previous"]) == (V2, V1)
    out = env.svc.rollback("al2023", "stable", approved_by="bob", reason="boot failure on Azure")
    assert out["version"] == V1 and out["previous"] == V2
    assert env.plat.channels[("al2023", "stable")] == V1
    assert "rollback" in out["reason"]


def test_rollback_needs_a_previous_and_refuses_missing_images(env):
    _stable(env, V1)
    with pytest.raises(PromotionError, match="no previous"):
        env.svc.rollback("al2023", "stable", approved_by="bob", reason="x")
    _stable(env, V2)
    env.plat.problems = ["us-east-1: ami-x not found"]
    with pytest.raises(PromotionError, match="not available"):
        env.svc.rollback("al2023", "stable", approved_by="bob", reason="x")
    assert env.svc.state("al2023", "stable")["version"] == V2      # state untouched on failure


def test_repromoting_current_version_keeps_previous(env):
    _stable(env, V1)
    _stable(env, V2)
    env.svc.promote("al2023", V2, "stable", approved_by="bob", no_open_sev2=True)
    assert env.svc.state("al2023", "stable")["previous"] == V1


def test_projection_failure_leaves_state_unchanged(env):
    env.make(V1)
    env.svc.promote("al2023", V1, "canary", approved_by="bob")
    env.plat.set_channel = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ssm down"))
    env.make(V2)
    with pytest.raises(RuntimeError):
        env.svc.promote("al2023", V2, "canary", approved_by="bob")
    assert env.svc.state("al2023", "canary")["version"] == V1


def test_retention_keeps_n_and_n1_deprecates_then_deletes_after_grace(env):
    old = "al2023-20260812.1"
    env.make(old)
    _stable(env, V1)
    _stable(env, V2)                      # stable=V2, previous=V1; `old` is unsupported
    plan = env.svc.retention_apply("al2023")
    assert plan == {"deprecate": [old], "delete": [], "delete_unreleased": []}
    assert mf.load(env.store, "al2023", old).deprecated_at
    with pytest.raises(PromotionError, match="deprecated"):
        env.svc.promote("al2023", old, "dev", approved_by="ci")
    env.clock.advance(59)
    assert env.svc.retention_plan("al2023")["delete"] == []
    env.clock.advance(1)
    plan = env.svc.retention_apply("al2023")
    assert plan["delete"] == [old] and env.plat.deregistered == [old]
    assert mf.load(env.store, "al2023", old).images == {"aws": {}}   # manifest and evidence remain


def test_gate_needs_every_built_platform_to_pass(env):
    m = env.make("al2023-20261112.2", gates=[])
    m.images["azure"] = {"gallery": "/subscriptions/x"}
    for g in ["goss", "compliance", "vuln_scan", "secrets", "sbom", "smoke"]:
        m.record_gate(g, "pass", platform="aws")
    mf.save(env.store, m)
    assert m.failing_gates(env.cfg.required_gates("canary")) == env.cfg.required_gates("canary")   # azure evidence missing
    m.record_gate("vuln_scan", "fail", "CVE-9 high", platform="azure")
    assert "azure: CVE-9 high" in m.gates["vuln_scan"]["detail"] and m.gates["vuln_scan"]["status"] == "fail"
    m.record_gate("vuln_scan", "pass", platform="azure")
    for g in ["goss", "compliance", "secrets", "sbom", "smoke"]:
        m.record_gate(g, "pass", platform="azure")
    assert m.failing_gates(env.cfg.required_gates("canary")) == []
