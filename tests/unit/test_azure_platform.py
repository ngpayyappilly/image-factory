"""Azure plugin, driven through a fake `az` runner (no Azure access, no SDK)."""
import json

import pytest

from imagefactory.platforms.azure import AzureCliError, AzurePlatform, norm_region
from imagefactory.versioning import Version

V = Version.parse("rhel9-20261112.1")        # Azure form: 2026.1112.1
CONFIG = {
    "subscription_id": "sub-1", "build_resource_group": "build-rg", "gallery_resource_group": "gal-rg",
    "gallery_name": "gal", "replication_regions": ["eastus2", "westus3"],
    "smoke_test": {"resource_group": "smoke-rg", "subnet_ids": {"eastus2": "/subnets/e", "westus3": "/subnets/w"},
                   "vm_size": "Standard_B2s", "timeout_seconds": 30},
}
DEF_ID = "/subscriptions/sub-1/resourceGroups/gal-rg/providers/Microsoft.Compute/galleries/gal/images/rhel9"
FLAVOR_CFG = {"os_family": "linux", "azure": {}}


class FakeAz:
    """Records every az invocation; replies from a table keyed by the command words."""

    def __init__(self, replies=None):
        self.calls, self.replies = [], replies or {}

    def __call__(self, cmd):
        assert cmd[0] == "az" and cmd[-2:] == ["-o", "json"]
        self.calls.append(cmd)
        key = " ".join(cmd[1:4])
        for k, v in self.replies.items():
            if key.startswith(k):
                if isinstance(v, Exception):
                    raise v
                return json.dumps(v(cmd) if callable(v) else v)
        return "null"

    def commands(self, prefix):
        return [c for c in self.calls if " ".join(c[1:]).startswith(prefix)]


def plat(az):
    p = AzurePlatform(CONFIG, run=az)
    p._sleep = lambda s: None
    return p


def good_version(**over):
    v = {"provisioningState": "Succeeded", "replicationStatus": {"aggregatedState": "Completed"},
         "publishingProfile": {"targetRegions": [{"name": "East US 2"}, {"name": "West US 3"}]}}
    v.update(over)
    return v


# --- identifiers -------------------------------------------------------------------------

def test_region_names_are_normalised():
    assert norm_region("East US 2") == norm_region("eastus2") == "eastus2"


def test_images_are_derived_from_config_not_from_the_packer_artifact():
    imgs = plat(FakeAz()).images_from_packer("whatever-packer-printed", "rhel9", V)
    assert imgs == {"eastus2": DEF_ID + "/versions/2026.1112.1", "westus3": DEF_ID + "/versions/2026.1112.1"}


def test_packer_vars_use_the_gallery_version_form(cfg):
    out = AzurePlatform(cfg.platform_cfg("azure")).packer_vars(cfg.image("rhel9"), V)
    assert out["azure_image_version"] == "2026.1112.1" and out["azure_gallery_name"] == "imagefactory"
    assert out["azure_image_publisher"] == "RedHat"


# --- verify_available --------------------------------------------------------------------

def test_verify_available_passes_for_a_replicated_version():
    az = FakeAz({"sig image-version show": good_version()})
    p = plat(az)
    assert p.verify_available(V, p.images_from_packer("", "rhel9", V)) == []
    cmd = az.commands("sig image-version show")[0]
    assert "--expand" in cmd and "ReplicationStatus" in cmd and "2026.1112.1" in cmd and "rhel9" in cmd


@pytest.mark.parametrize("override,needle", [
    ({"provisioningState": "Creating"}, "Creating, not Succeeded"),
    ({"replicationStatus": {"aggregatedState": "InProgress"}}, "replication is InProgress"),
    ({"publishingProfile": {"targetRegions": [{"name": "East US 2"}]}}, "westus3 is not a target region"),
])
def test_verify_available_reports_each_failure(override, needle):
    p = plat(FakeAz({"sig image-version show": good_version(**override)}))
    assert any(needle in x for x in p.verify_available(V, p.images_from_packer("", "rhel9", V)))


def test_verify_available_reports_a_missing_version_and_a_missing_region():
    p = plat(FakeAz({"sig image-version show": AzureCliError("(ResourceNotFound) version does not exist")}))
    problems = p.verify_available(V, {"eastus2": "x"})
    assert any("not found in gallery" in x for x in problems) and any("westus3: no image replicated" in x for x in problems)


def test_verify_available_does_not_swallow_other_cli_errors():
    p = plat(FakeAz({"sig image-version show": AzureCliError("AuthorizationFailed")}))
    with pytest.raises(AzureCliError, match="AuthorizationFailed"):
        p.verify_available(V, {})


# --- channels ----------------------------------------------------------------------------

def test_set_channel_merges_tags_on_the_image_definition_atomically():
    az = FakeAz()
    plat(az).set_channel("rhel9", "stable", V, {})
    (cmd,) = az.commands("tag update")
    assert cmd[cmd.index("--resource-id") + 1] == DEF_ID
    assert cmd[cmd.index("--operation") + 1] == "Merge"                      # only these tags change, in one call
    tags = cmd[cmd.index("--tags") + 1:cmd.index("--only-show-errors")]
    assert tags == ["imagefactory-stable=2026.1112.1", "imagefactory-stable-version=rhel9-20261112.1"]


# --- retention ---------------------------------------------------------------------------

def test_deregister_deletes_the_version_and_tolerates_already_gone():
    az = FakeAz()
    plat(az).deregister(V, {})
    assert "2026.1112.1" in az.commands("sig image-version delete")[0]
    plat(FakeAz({"sig image-version delete": AzureCliError("(ResourceNotFound) gone")})).deregister(V, {})   # idempotent
    with pytest.raises(AzureCliError):
        plat(FakeAz({"sig image-version delete": AzureCliError("AuthorizationFailed")})).deregister(V, {})


# --- smoke test --------------------------------------------------------------------------

def agent(status):
    return {"instanceView": {"vmAgent": {"statuses": [{"displayStatus": status}]}}}


@pytest.fixture(autouse=True)
def no_real_ssh_keygen(monkeypatch):
    monkeypatch.setattr(AzurePlatform, "_auth_args",
                        lambda self, os_family, workdir: (["--admin-password", "pw"] if os_family == "windows"
                                                          else ["--authentication-type", "ssh", "--ssh-key-values", "k.pub"]))


def run_smoke(az, flavor_cfg=FLAVOR_CFG, images=None):
    p = plat(az)
    return p.smoke_test("rhel9", V, images or p.images_from_packer("", "rhel9", V), flavor_cfg)


def test_smoke_pass_boots_privately_waits_for_the_agent_and_cleans_up():
    az = FakeAz({"vm get-instance-view": agent("Ready")})
    assert run_smoke(az) == []
    creates = az.commands("vm create")
    assert len(creates) == 2                                          # one per replication region
    c = creates[0]
    assert c[c.index("--image") + 1] == DEF_ID + "/versions/2026.1112.1"
    assert c[c.index("--public-ip-address") + 1] == "" and c[c.index("--nsg") + 1] == ""     # no inbound exposure
    assert "Delete" in c and "--os-disk-delete-option" in c and "--nic-delete-option" in c
    assert "imagefactory-managed=true" in c
    assert len(az.commands("vm delete")) == 2


def test_smoke_agent_never_ready_fails_and_still_deletes_the_vm():
    az = FakeAz({"vm get-instance-view": agent("Not Ready")})
    problems = run_smoke(az)
    assert len(problems) == 2 and "never reported Ready" in problems[0]
    assert len(az.commands("vm delete")) == 2


def test_smoke_create_failure_is_a_problem_not_a_crash_and_still_cleans_up():
    az = FakeAz({"vm create": AzureCliError("(QuotaExceeded) cores")})
    problems = run_smoke(az)
    assert all("could not be created" in x and "QuotaExceeded" in x for x in problems)
    assert len(az.commands("vm delete")) == 2                         # a half-created VM must not leak


def test_smoke_cleanup_failure_is_not_hidden_but_not_found_is_fine():
    az = FakeAz({"vm get-instance-view": agent("Ready"), "vm delete": AzureCliError("(ResourceNotFound) gone")})
    assert run_smoke(az) == []
    az = FakeAz({"vm get-instance-view": agent("Ready"), "vm delete": AzureCliError("AuthorizationFailed")})
    with pytest.raises(AzureCliError):
        run_smoke(az)


def test_smoke_reports_configuration_gaps_instead_of_skipping():
    p = AzurePlatform({**CONFIG, "smoke_test": {}}, run=FakeAz())
    assert "resource_group is not configured" in p.smoke_test("rhel9", V, {"eastus2": "x"}, FLAVOR_CFG)[0]
    problems = run_smoke(FakeAz(), images={"eastus2": "i", "uksouth": "j"})
    assert any(x == "uksouth: platforms.azure.smoke_test.subnet_ids has no subnet for this region" for x in problems)


def test_smoke_windows_uses_a_password_and_linux_uses_ssh():
    az = FakeAz({"vm get-instance-view": agent("Ready")})
    run_smoke(az, flavor_cfg={"os_family": "windows", "azure": {"smoke_vm_size": "Standard_D4s_v5"}})
    c = az.commands("vm create")[0]
    assert "--admin-password" in c and c[c.index("--size") + 1] == "Standard_D4s_v5"
    az = FakeAz({"vm get-instance-view": agent("Ready")})
    run_smoke(az)
    assert "--ssh-key-values" in az.commands("vm create")[0]
