"""Our own Ansible YAML must parse. `ansible-playbook --syntax-check` only parses what it loads statically, so a task
file pulled in dynamically can hide a syntax error until a real build is 50 minutes in (it happened)."""
import glob

import yaml


def test_every_ansible_yaml_file_in_the_repo_parses():
    files = [f for f in glob.glob("ansible/**/*.yml", recursive=True) if "/external/" not in f]
    assert len(files) >= 6                         # the playbook, both roles, the vars files, requirements
    for f in files:
        try:
            yaml.safe_load(open(f))
        except yaml.YAMLError as e:                # name the file; a bare YAML error does not
            raise AssertionError("%s does not parse: %s" % (f, str(e).splitlines()[0]))


def test_cis_override_variables_are_prefixed_for_their_role():
    # A typo'd override silently does nothing; at least make sure the file only holds variables for its own role.
    for flavor, prefix in (("al2023", "amzn2023cis_"), ("rhel9", "rhel9cis_")):
        vars_ = yaml.safe_load(open("ansible/vars/cis_%s.yml" % flavor))
        for k in vars_:
            assert k == "cis_overrides_loaded" or k.startswith(prefix), "%s: %r is not a %s variable" % (flavor, k, prefix)
