"""GCP plugin, driven through a fake `gcloud` runner (no GCP access, no SDK)."""
import json
from pathlib import Path

import pytest

from imagefactory.platforms.gcp import DEFAULT_BOOT_PATTERN, GcloudError, GcpPlatform
from imagefactory.versioning import Version

V = Version.parse("rhel9-20261112.1")          # GCE name: rhel9-20261112-1
NAME = "rhel9-20261112-1"
CONFIG = {
    "project_id": "proj-1", "zone": "us-central1-a", "image_storage_locations": ["us"],
    "smoke_test": {"subnetwork": "projects/proj-1/regions/us-central1/subnetworks/smoke", "machine_type": "e2-medium",
                   "timeout_seconds": 30},
}
FLAVOR_CFG = {"os_family": "linux", "gcp": {}}
BOOTED = "[ OK ] Started Google Compute Engine Guest Agent.\n"


class FakeGcloud:
    """Records each invocation; replies from a table keyed by the command words."""

    def __init__(self, replies=None):
        self.calls, self.replies, self.files = [], replies or {}, {}

    def __call__(self, cmd):
        assert cmd[0] == "gcloud" and "--quiet" in cmd and cmd[cmd.index("--project") + 1] == "proj-1"
        self.calls.append(cmd)
        if "--data-file" in cmd:                         # capture the payload before the temp dir disappears
            self.files[cmd[cmd.index("--data-file") + 1]] = Path(cmd[cmd.index("--data-file") + 1]).read_text()
        key = " ".join(cmd[1:4])
        for k, v in self.replies.items():
            if key.startswith(k):
                if isinstance(v, Exception):
                    raise v
                v = v(cmd) if callable(v) else v
                return v if isinstance(v, str) else json.dumps(v)
        return ""

    def commands(self, prefix):
        return [c for c in self.calls if " ".join(c[1:]).startswith(prefix)]


def plat(g):
    p = GcpPlatform(CONFIG, run=g)
    p._sleep = lambda s: None
    return p


def good_image(**over):
    img = {"status": "READY", "labels": {"imagefactory-version": NAME}, "storageLocations": ["us"]}
    img.update(over)
    return img


# --- identifiers ----------------------------------------------------------------------------

def test_image_references_are_derived_from_project_and_name():
    assert plat(FakeGcloud()).images_from_packer("ignored", "rhel9", V) == {"us": "projects/proj-1/global/images/" + NAME}
    no_loc = GcpPlatform({**CONFIG, "image_storage_locations": []}, run=FakeGcloud())
    assert list(no_loc.images_from_packer("", "rhel9", V)) == ["global"]          # GCE images are global by default


def test_secret_names_follow_the_prefix_flavor_channel():
    assert plat(FakeGcloud()).secret_name("rhel9", "stable") == "imagefactory-rhel9-stable"
    assert GcpPlatform({**CONFIG, "channel_secret_prefix": "golden"}).secret_name("a", "dev") == "golden-a-dev"


def test_packer_vars_map_config_including_private_build_settings(cfg):
    out = GcpPlatform(cfg.platform_cfg("gcp")).packer_vars(cfg.image("rhel9"), V)
    assert out["gcp_image_name"] == NAME and out["gcp_source_image_family"] == "rhel-9"
    assert out["gcp_use_internal_ip"] is False and out["gcp_omit_external_ip"] is False
    assert out["gcp_subnetwork"] == "" and out["gcp_build_service_account"] == ""


# --- verify_available -----------------------------------------------------------------------

def test_verify_available_passes_for_a_ready_labelled_image():
    g = FakeGcloud({"compute images describe": good_image()})
    p = plat(g)
    assert p.verify_available(V, p.images_from_packer("", "rhel9", V)) == []
    assert NAME in g.commands("compute images describe")[0]


@pytest.mark.parametrize("override,needle", [
    ({"status": "PENDING"}, "PENDING, not READY"),
    ({"deprecated": {"state": "DEPRECATED"}}, "is DEPRECATED"),
    ({"labels": {"imagefactory-version": "other"}}, "is labelled 'other'"),
    ({"labels": {}}, "is labelled None"),
    ({"storageLocations": ["eu"]}, "not stored in us"),
])
def test_verify_available_reports_each_failure(override, needle):
    p = plat(FakeGcloud({"compute images describe": good_image(**override)}))
    assert any(needle in x for x in p.verify_available(V, p.images_from_packer("", "rhel9", V)))


def test_verify_available_reports_missing_image_and_unrecorded_location():
    p = plat(FakeGcloud({"compute images describe": GcloudError("ERROR: The resource 'projects/p/global/images/x' was not found")}))
    problems = p.verify_available(V, {})
    assert any("not found in project" in x for x in problems) and any("us: no image recorded" in x for x in problems)


def test_verify_available_does_not_swallow_other_errors():
    with pytest.raises(GcloudError, match="PERMISSION_DENIED"):
        plat(FakeGcloud({"compute images describe": GcloudError("PERMISSION_DENIED")})).verify_available(V, {})


# --- channels -------------------------------------------------------------------------------

def test_set_channel_adds_a_secret_version_with_the_pointer():
    g = FakeGcloud()
    plat(g).set_channel("rhel9", "stable", V, {})
    (cmd,) = g.commands("secrets versions add")
    assert cmd[4] == "imagefactory-rhel9-stable"
    payload = json.loads(next(iter(g.files.values())))
    assert payload == {"image": NAME, "project": "proj-1", "version": "rhel9-20261112.1"}


# --- retention ------------------------------------------------------------------------------

def test_deregister_deletes_the_image_and_tolerates_already_gone():
    g = FakeGcloud()
    plat(g).deregister(V, {})
    assert NAME in g.commands("compute images delete")[0]
    plat(FakeGcloud({"compute images delete": GcloudError("was not found")})).deregister(V, {})     # idempotent
    with pytest.raises(GcloudError):
        plat(FakeGcloud({"compute images delete": GcloudError("PERMISSION_DENIED")})).deregister(V, {})


# --- smoke test -----------------------------------------------------------------------------

def run_smoke(g, config=None):
    p = GcpPlatform(config or CONFIG, run=g)
    p._sleep = lambda s: None
    return p.smoke_test("rhel9", V, p.images_from_packer("", "rhel9", V), FLAVOR_CFG)


def test_smoke_pass_boots_privately_waits_for_the_guest_agent_and_cleans_up():
    g = FakeGcloud({"compute instances get-serial-port-output": BOOTED})
    assert run_smoke(g) == []
    (c,) = g.commands("compute instances create")
    assert c[c.index("--image") + 1] == NAME and c[c.index("--image-project") + 1] == "proj-1"
    assert "--no-address" in c and c[c.index("--subnet") + 1].endswith("/subnetworks/smoke")
    assert "imagefactory-managed=true" in c[c.index("--labels") + 1]
    (d,) = g.commands("compute instances delete")
    assert d[d.index("--delete-disks") + 1] == "all"
    assert g.commands("compute instances get-serial-port-output")[0][-1] != "--format=json"      # raw text, not JSON


def test_smoke_boots_once_because_gce_images_are_global():
    g = FakeGcloud({"compute instances get-serial-port-output": BOOTED})
    p = GcpPlatform({**CONFIG, "image_storage_locations": ["us", "eu"]}, run=g)
    p._sleep = lambda s: None
    p.smoke_test("rhel9", V, p.images_from_packer("", "rhel9", V), FLAVOR_CFG)
    assert len(g.commands("compute instances create")) == 1


def test_smoke_never_booted_fails_and_still_deletes_the_vm():
    g = FakeGcloud({"compute instances get-serial-port-output": "kernel panic\n"})
    (problem,) = run_smoke(g)
    assert "never showed" in problem and DEFAULT_BOOT_PATTERN in problem
    assert len(g.commands("compute instances delete")) == 1


def test_smoke_serial_output_unavailable_yet_is_retried_not_fatal():
    outputs = iter([GcloudError("instance not ready"), GcloudError("instance not ready"), BOOTED])

    def serial(cmd):
        o = next(outputs)
        if isinstance(o, Exception):
            raise o
        return o
    slow = {**CONFIG, "smoke_test": {**CONFIG["smoke_test"], "timeout_seconds": 90}}          # room for 3 polls
    assert run_smoke(FakeGcloud({"compute instances get-serial-port-output": serial}), slow) == []


def test_smoke_create_failure_is_a_problem_not_a_crash_and_still_cleans_up():
    g = FakeGcloud({"compute instances create": GcloudError("QUOTA_EXCEEDED: CPUS")})
    (problem,) = run_smoke(g)
    assert "could not be created" in problem and "QUOTA_EXCEEDED" in problem
    assert len(g.commands("compute instances delete")) == 1                    # a half-created VM must not leak


def test_smoke_cleanup_failure_is_not_hidden_but_not_found_is_fine():
    ok = {"compute instances get-serial-port-output": BOOTED}
    assert run_smoke(FakeGcloud({**ok, "compute instances delete": GcloudError("was not found")})) == []
    with pytest.raises(GcloudError):
        run_smoke(FakeGcloud({**ok, "compute instances delete": GcloudError("PERMISSION_DENIED")}))


def test_smoke_requires_a_subnetwork_instead_of_silently_skipping():
    g = FakeGcloud()
    assert "subnetwork is not configured" in run_smoke(g, {**CONFIG, "smoke_test": {}})[0]
    assert g.calls == []
