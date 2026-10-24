import json

from imagefactory import gates


def goss(results, failed=None):
    failed = sum(1 for r in results if not r["successful"]) if failed is None else failed
    return {"results": results, "summary": {"failed-count": failed, "test-count": len(results)}}


def test_goss_pass_fail_and_empty():
    assert gates.evaluate_goss(goss([{"successful": True}]))[0] == "pass"
    status, detail = gates.evaluate_goss(goss([{"successful": False, "title": "sshd root login"}]))
    assert status == "fail" and "sshd root login" in detail
    assert gates.evaluate_goss(goss([]))[0] == "fail"        # a run with zero tests must not pass


def test_compliance_threshold_and_exceptions():
    rs = [{"successful": True, "title": "a"}] * 8 + [{"successful": False, "title": "x1"}, {"successful": False, "title": "x2"}]
    assert gates.evaluate_compliance(goss(rs), 0.90)[0] == "fail"          # 0.8 < 0.9
    assert gates.evaluate_compliance(goss(rs), 0.80)[0] == "pass"
    assert gates.evaluate_compliance(goss(rs), 0.90, excepted={"x1", "x2"})[0] == "pass"
    assert gates.evaluate_compliance(goss([]), 0.9)[0] == "fail"


def trivy(vulns=(), secrets=()):
    return {"Results": [{"Target": "os", "Vulnerabilities": [{"VulnerabilityID": i, "Severity": s} for i, s in vulns],
                         "Secrets": [{"RuleID": r} for r in secrets]}]}


def test_vulns_block_critical_and_high_only():
    assert gates.evaluate_vulns(trivy([("CVE-1", "MEDIUM"), ("CVE-2", "LOW")]))[0] == "pass"
    assert gates.evaluate_vulns(trivy([("CVE-3", "HIGH")]))[0] == "fail"
    assert gates.evaluate_vulns(trivy([("CVE-4", "CRITICAL")]))[0] == "fail"
    assert gates.evaluate_vulns(trivy([("CVE-4", "CRITICAL")]), excepted={"CVE-4"})[0] == "pass"
    assert gates.evaluate_vulns({})[0] == "pass"             # trivy emits no Results when clean


def test_secrets():
    assert gates.evaluate_secrets(trivy())[0] == "pass"
    assert gates.evaluate_secrets(trivy(secrets=["aws-access-key-id"]))[0] == "fail"


def test_sbom_formats():
    assert gates.evaluate_sbom({"bomFormat": "CycloneDX", "components": [{"name": "bash"}]})[0] == "pass"
    assert gates.evaluate_sbom({"spdxVersion": "SPDX-2.3", "packages": [{"name": "bash"}]})[0] == "pass"
    assert gates.evaluate_sbom({"bomFormat": "CycloneDX", "components": []})[0] == "fail"
    assert gates.evaluate_sbom({"hello": "world"})[0] == "fail"


def test_ingest_skips_absent_reports_and_fails_unparseable(tmp_path):
    from imagefactory.config import Config
    cfg = Config.load()
    (tmp_path / "goss.json").write_text(json.dumps(goss([{"successful": True}])))
    (tmp_path / "sbom.json").write_text("not json")
    out = gates.ingest(tmp_path, cfg)
    assert out["goss"][0] == "pass"
    assert out["sbom"][0] == "fail"
    assert "vuln_scan" not in out and "compliance" not in out   # absent evidence leaves the gate unmet


def test_signing_roundtrip_and_failure_modes():
    import pytest
    from imagefactory import manifest as mf, signing
    m = mf.Manifest(version="al2023-20261112.1", flavor="al2023", baseline_version="1", commit="c", pipeline_run="1",
                    author="a", created_at="2026-11-12T00:00:00Z", images={"aws": {"r": "ami-1"}})

    def ok(cmd):
        if cmd[1] == "sign-blob":
            open(cmd[cmd.index("--output-signature") + 1], "wb").write(b"sig")
    m.signature = signing.sign(m, "awskms:///alias/x", run=ok)
    signing.verify(m, "awskms:///alias/x", run=ok)

    with pytest.raises(signing.SigningError, match="no signature"):
        signing.sign(m, "k", run=lambda cmd: None)                  # cosign "succeeded" but wrote nothing
    m.signature = None
    with pytest.raises(signing.SigningError, match="not signed"):
        signing.verify(m, "k", run=ok)

    # Gate results and channel state are lifecycle data: changing them must not invalidate the signature,
    # but changing what the image IS (its AMIs) must.
    a = m.canonical_bytes()
    m.record_gate("goss", "pass")
    assert m.canonical_bytes() == a
    m.images["aws"]["r"] = "ami-tampered"
    assert m.canonical_bytes() != a


def test_ingest_finds_reports_in_a_nested_directory(tmp_path):
    """Packer's download put the files under <dest>/imagefactory-reports/, and ingest used to look only at <dest>."""
    from imagefactory.config import Config
    nested = tmp_path / "amazon-ebs" / "imagefactory-reports"
    nested.mkdir(parents=True)
    (nested / "goss.json").write_text(json.dumps(goss([{"successful": True}])))
    (nested / "sbom.json").write_text(json.dumps({"bomFormat": "CycloneDX", "components": [{"name": "bash"}]}))
    out = gates.ingest(tmp_path / "amazon-ebs", Config.load())
    assert out["goss"][0] == "pass" and out["sbom"][0] == "pass"


def test_signing_never_touches_the_public_transparency_log():
    """cosign uploads every signature to the public Sigstore Rekor log by default (found on the first real signing)."""
    from imagefactory import manifest as mf, signing
    m = mf.Manifest(version="al2023-20261112.1", flavor="al2023", baseline_version="1", commit="c", pipeline_run="1",
                    author="a", created_at="2026-11-12T00:00:00Z", images={"aws": {"r": "ami-1"}})
    seen = []

    def run(cmd):
        seen.append(cmd)
        if cmd[1] == "sign-blob":
            open(cmd[cmd.index("--output-signature") + 1], "wb").write(b"sig")
    m.signature = signing.sign(m, "awskms:///alias/x", run=run)
    signing.verify(m, "awskms:///alias/x", run=run)
    sign_cmd, verify_cmd = seen
    assert "--tlog-upload=false" in sign_cmd
    assert "--insecure-ignore-tlog=true" in verify_cmd
