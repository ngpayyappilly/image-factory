"""Our own Ansible YAML must parse. `ansible-playbook --syntax-check` only parses what it loads statically, so a task
file pulled in dynamically can hide a syntax error until a real build is 50 minutes in (it happened)."""
import glob

import pytest
import yaml


def test_every_ansible_yaml_file_in_the_repo_parses():
    files = [f for f in glob.glob("ansible/**/*.yml", recursive=True) if "/external/" not in f]
    assert len(files) >= 6                         # the playbook, both roles, the vars files, requirements
    for f in files:
        try:
            yaml.safe_load(open(f))
        except yaml.YAMLError as e:                # name the file; a bare YAML error does not
            raise AssertionError("%s does not parse: %s" % (f, str(e).splitlines()[0]))


ROLES = (("al2023", "amzn2023cis_", "AMAZON2023-CIS"), ("rhel9", "rhel9cis_", "RHEL9-CIS"), ("ubuntu2404", "ubtu24cis_", "UBUNTU24-CIS"))
ROLE_LEVEL_VARS = {"cis_overrides_loaded", "system_is_ec2"}      # the one marker we add, and a variable shared by the lockdown roles


def test_cis_override_variables_are_prefixed_for_their_role():
    # A typo'd override silently does nothing; at least make sure each file only holds variables for its own role.
    for flavor, prefix, _ in ROLES:
        for k in yaml.safe_load(open("ansible/vars/cis_%s.yml" % flavor)):
            assert k in ROLE_LEVEL_VARS or k.startswith(prefix), "%s: %r is not a %s variable" % (flavor, k, prefix)


def test_cis_override_variables_exist_in_the_pinned_role():
    """A misspelt override is ignored by Ansible without a word. Check against the real role defaults when the roles are installed
    (`ansible-galaxy role install -r ansible/requirements.yml -p ansible/external`; scripts/build.sh does this)."""
    import os
    checked = 0
    for flavor, _, role in ROLES:
        d = "ansible/external/%s/defaults/main" % role
        if not os.path.isdir(d):
            continue
        defined = set()
        for f in glob.glob(d + "/*.yml"):
            defined |= set(yaml.safe_load(open(f)) or {})
        for k in yaml.safe_load(open("ansible/vars/cis_%s.yml" % flavor)):
            if k != "cis_overrides_loaded":
                assert k in defined, "%s: %r is not a variable of %s (misspelt? ignored silently)" % (flavor, k, role)
                checked += 1
    if not checked:
        pytest.skip("CIS roles are not installed in ansible/external")
