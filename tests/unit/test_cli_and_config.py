import json

import pytest

from imagefactory import cli
from imagefactory.config import Config, ConfigError
from imagefactory.platforms import PlatformNotImplemented, get_platform

LOCAL = """
store: {type: local, local_path: %s}
"""


@pytest.fixture
def local_cfg(tmp_path):
    """The shipped config with the store switched to a temp dir (and signing disabled via fakes)."""
    import yaml
    raw = yaml.safe_load(open("config/images.yaml"))
    raw["store"] = {"type": "local", "local_path": str(tmp_path / "store")}
    p = tmp_path / "images.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def test_shipped_config_is_valid_and_aws_only_by_default(cfg):
    assert cfg.enabled_platforms("rhel9") == ["aws"]        # azure/gcp/vmware are listed but switched off
    assert cfg.enabled_platforms("al2023") == ["aws"]


def test_exceptions_must_be_time_boxed_and_approved(tmp_path):
    import yaml
    raw = yaml.safe_load(open("config/images.yaml"))
    (tmp_path / "images.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / "exceptions.yaml").write_text(yaml.safe_dump({"exceptions": [{"id": "CVE-1", "gate": "vuln_scan"}]}))
    with pytest.raises(ConfigError, match="time-boxed"):
        Config.load(tmp_path / "images.yaml")


def test_expired_exceptions_stop_applying(tmp_path):
    import datetime as dt
    c = Config({"images": {}}, [{"id": "CVE-1", "gate": "vuln_scan", "reason": "r", "approved_by": "sec", "expires": "2026-11-01"}])
    assert c.active_exceptions("vuln_scan", dt.date(2026, 11, 1)) == {"CVE-1"}
    assert c.active_exceptions("vuln_scan", dt.date(2026, 11, 2)) == set()
    assert c.active_exceptions("secrets", dt.date(2026, 11, 1)) == set()


def test_optional_platforms_map_packer_vars_but_fail_loudly_at_runtime(cfg):
    # AWS, Azure and GCP have real runtimes (their own test modules); VMware still only maps Packer vars.
    for name, key in (("vmware", "vsphere_template"),):
        plat = get_platform(name, cfg)
        from imagefactory.versioning import Version
        assert key in plat.packer_vars(cfg.image("rhel9"), Version.parse("rhel9-20261112.3"))
        with pytest.raises(PlatformNotImplemented):
            plat.set_channel("rhel9", "stable", Version.parse("rhel9-20261112.3"), {})


def test_cli_end_to_end_without_cloud(local_cfg, tmp_path, capsys):
    run = lambda *a: cli.main(["--config", str(local_cfg), *a])

    assert run("version-next", "--flavor", "al2023", "--date", "2026-11-12") == 0
    assert capsys.readouterr().out.strip() == "al2023-20261112.1"

    assert run("packer-vars", "--flavor", "al2023", "--platform", "aws", "--version", "al2023-20261112.1", "--commit", "abc") == 0
    v = json.loads(capsys.readouterr().out)
    assert v["image_version"] == "al2023-20261112.1" and v["cis_role"] == "AMAZON2023-CIS"

    pm = tmp_path / "packer-manifest.json"
    pm.write_text(json.dumps({"builds": [{"builder_type": "amazon-ebs", "artifact_id": "us-east-1:ami-1,us-west-2:ami-2"}]}))
    build = ["--flavor", "al2023", "--version", "al2023-20261112.1", "--platform", "aws"]
    assert run("manifest-create", *build, "--packer-manifest", str(pm), "--commit", "abc", "--run-id", "7", "--author", "alice") == 0
    assert "recorded" in capsys.readouterr().out
    # An unreleased build still owns its version number: the next build must not reuse it.
    assert run("version-next", "--flavor", "al2023", "--date", "2026-11-12") == 0
    assert capsys.readouterr().out.strip() == "al2023-20261112.2"
    # ...and an image is never re-recorded.
    with pytest.raises(SystemExit, match="already recorded"):
        run("manifest-create", *build, "--packer-manifest", str(pm), "--commit", "abc", "--run-id", "8", "--author", "alice")

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "goss.json").write_text(json.dumps({"results": [{"successful": False, "title": "t"}],
                                                   "summary": {"failed-count": 1, "test-count": 1}}))
    assert run("gate-ingest", *build, "--reports", str(reports)) == 1       # a failing gate fails the build step

    # Not released, so there is no manifest to promote, and `release` skips it and exits non-zero.
    assert run("promote", "--flavor", "al2023", "--version", "al2023-20261112.1", "--to", "canary", "--approved-by", "bob") == 2
    assert "no manifest" in capsys.readouterr().err
    matrix = json.dumps({"al2023_aws": {"flavor": "al2023", "platform": "aws", "version": "al2023-20261112.1"}})
    assert run("release", "--matrix", matrix, "--run-id", "9") == 1
    captured = capsys.readouterr()
    assert "SKIPPED" in captured.out and "gates not passing" in captured.out


def test_plan_gives_one_version_per_flavor_and_a_matrix_entry_per_platform(local_cfg, capsys):
    assert cli.main(["--config", str(local_cfg), "plan", "--flavors", "al2023,rhel9", "--date", "2026-11-12"]) == 0
    m = json.loads(capsys.readouterr().out)
    assert set(m) == {"al2023_aws", "rhel9_aws"}              # optional platforms are off by default
    assert m["rhel9_aws"] == {"flavor": "rhel9", "platform": "aws", "version": "rhel9-20261112.1"}


def test_plan_emits_ado_output_variable(local_cfg, capsys):
    cli.main(["--config", str(local_cfg), "plan", "--flavors", "al2023", "--date", "2026-11-12", "--ado-variable", "matrix"])
    assert capsys.readouterr().out.startswith("##vso[task.setvariable variable=matrix;isOutput=true]{")


def test_plan_writes_github_outputs(local_cfg, tmp_path, monkeypatch, capsys):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    cli.main(["--config", str(local_cfg), "plan", "--flavors", "al2023,rhel9", "--date", "2026-11-12", "--github-output"])
    lines = dict(l.split("=", 1) for l in out.read_text().splitlines())
    inc = json.loads(lines["include"])["include"]
    assert {e["flavor"] for e in inc} == {"al2023", "rhel9"} and lines["any"] == "true"
    assert set(json.loads(lines["matrix"])) == {"al2023_aws", "rhel9_aws"}      # the shape `release` consumes


def test_evidence_upload_keys_and_local_store_skip(local_cfg, tmp_path, monkeypatch, capsys):
    from imagefactory.store import S3Store
    reports = tmp_path / "reports" / "al2023-20261112.1" / "amazon-ebs"
    reports.mkdir(parents=True)
    (reports / "goss.json").write_text("{}")
    args = ["evidence-upload", "--flavor", "al2023", "--version", "al2023-20261112.1", "--reports", str(tmp_path / "reports" / "al2023-20261112.1")]
    assert cli.main(["--config", str(local_cfg), *args]) == 0
    assert "stays local" in capsys.readouterr().out

    puts = []

    class S3:
        def put_object(self, **kw):
            puts.append(kw)
    monkeypatch.setattr(cli, "store_from_config", lambda cfg: S3Store("b", "", client=S3()))
    assert cli.main(["--config", str(local_cfg), *args]) == 0
    assert puts[0]["Key"] == "evidence/al2023/al2023-20261112.1/amazon-ebs/goss.json"
    assert puts[0]["ServerSideEncryption"] == "aws:kms"


# ---- CPU architecture -------------------------------------------------------------------

def _cfg_with(tmp_path, **image_overrides):
    import yaml
    raw = yaml.safe_load(open("config/images.yaml"))
    raw["images"]["bad"] = {"os_family": "linux", "arch": "arm64", "platforms": ["aws"], "aws": {
        "source_ami_name": "x-{arch}", "source_ami_owner": "amazon"}, **image_overrides}
    (tmp_path / "images.yaml").write_text(yaml.safe_dump(raw))
    return tmp_path / "images.yaml"


def test_shipped_flavors_declare_architecture(cfg):
    assert cfg.arch("al2023") == "x86_64" and cfg.arch("al2023arm") == "arm64" and cfg.arch("rhel9") == "x86_64"
    assert cfg.enabled_platforms("al2023arm") == ["aws"]


def test_unsupported_architecture_combinations_are_rejected_at_load(tmp_path):
    with pytest.raises(ConfigError, match="not one of"):
        Config.load(_cfg_with(tmp_path, arch="riscv"))
    with pytest.raises(ConfigError, match="aws only"):
        Config.load(_cfg_with(tmp_path, platforms=["aws", "azure"], azure={}))
    with pytest.raises(ConfigError, match="Linux flavors only"):
        Config.load(_cfg_with(tmp_path, os_family="windows"))


def test_aws_packer_vars_follow_the_architecture(cfg):
    from imagefactory.versioning import Version
    aws = get_platform("aws", cfg)
    arm = aws.packer_vars(cfg.image("al2023arm"), Version.parse("al2023arm-20261112.1"))
    x86 = aws.packer_vars(cfg.image("al2023"), Version.parse("al2023-20261112.1"))
    assert arm["aws_source_ami_name"] == "al2023-ami-2023.*-kernel-*-arm64" and arm["aws_instance_type"] == "t4g.medium"
    assert x86["aws_source_ami_name"] == "al2023-ami-2023.*-kernel-*-x86_64" and x86["aws_instance_type"] == "t3.medium"


def test_cli_packer_vars_and_matrix_carry_architecture(local_cfg, capsys):
    run = lambda *a: cli.main(["--config", str(local_cfg), *a])
    assert run("packer-vars", "--flavor", "al2023arm", "--platform", "aws", "--version", "al2023arm-20261112.1") == 0
    assert json.loads(capsys.readouterr().out)["architecture"] == "arm64"
    assert run("matrix", "--flavor", "al2023arm") == 0
    assert json.loads(capsys.readouterr().out)[0]["arch"] == "arm64"
    assert run("plan", "--flavors", "al2023,al2023arm", "--date", "2026-11-12") == 0
    m = json.loads(capsys.readouterr().out)
    # Same OS, different architecture => different flavor, different version stream, different channels.
    assert m["al2023_aws"]["version"] == "al2023-20261112.1" and m["al2023arm_aws"]["version"] == "al2023arm-20261112.1"


def test_architecture_is_part_of_the_signed_identity():
    from imagefactory import manifest as mf
    kw = dict(version="al2023arm-20261112.1", flavor="al2023arm", baseline_version="1", commit="c", pipeline_run="1",
              author="a", created_at="2026-11-12T00:00:00Z", images={"aws": {"r": "ami-1"}})
    assert mf.Manifest(arch="arm64", **kw).canonical_bytes() != mf.Manifest(arch="x86_64", **kw).canonical_bytes()
    assert mf.Manifest(**kw).arch == "x86_64"        # manifests written before this field existed still load
