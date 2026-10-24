"""goss rejects malformed tests at run time ("Syntax Error: Missing required attribute"), which we only see 17+ minutes
into a real build and cannot reproduce locally (goss is Linux-only). So render the templates here, for every Linux flavor
in the config, and check the rules statically."""
import re

import pytest
import yaml

from imagefactory.config import Config

GOSS_FILES = ("tests/goss/linux.yaml", "tests/goss/cis.yaml")
REQUIRED = {                      # resource type -> attributes goss insists on
    "command": ["exec", "exit-status"],
    "file": ["exists"],
    "package": ["installed"],
    "kernel-param": ["value"],
}
IF_RE = re.compile(r"^\s*\{\{\s*if\s+(.+?)\s*\}\}\s*$")
ELSE_RE = re.compile(r"^\s*\{\{\s*else\s*\}\}\s*$")
END_RE = re.compile(r"^\s*\{\{\s*end\s*\}\}\s*$")
ENV_RE = re.compile(r"\{\{\s*\.Env\.(\w+)\s*\}\}")


def _cond(expr, env):
    m = re.fullmatch(r'eq \.Env\.(\w+) "([^"]*)"', expr)
    if m:
        return env[m.group(1)] == m.group(2)
    m = re.fullmatch(r'hasPrefix "([^"]*)" \.Env\.(\w+)', expr)
    if m:
        return env[m.group(2)].startswith(m.group(1))
    raise AssertionError("template condition not supported by this test (extend it, do not skip it): %r" % expr)


def render(path, env):
    """The small subset of Go templating our goss files use: if / else / end on their own lines, and inline .Env.X."""
    out, stack = [], []
    for line in open(path).read().splitlines():
        if IF_RE.match(line):
            stack.append(_cond(IF_RE.match(line).group(1), env))
        elif ELSE_RE.match(line):
            stack[-1] = not stack[-1]
        elif END_RE.match(line):
            stack.pop()
        elif all(stack):
            out.append(ENV_RE.sub(lambda m: env[m.group(1)], line))
    assert not stack, "%s: unbalanced {{ if }}/{{ end }}" % path
    text = "\n".join(out)
    assert "{{" not in text and "}}" not in text, "%s: template syntax this test cannot render" % path
    return text


class NoDuplicateKeys(yaml.SafeLoader):
    """goss silently keeps only the LAST of a duplicated key, dropping the earlier tests: refuse them."""


def _mapping(loader, node):
    keys = [loader.construct_object(k) for k, _ in node.value]
    dup = {k for k in keys if keys.count(k) > 1}
    assert not dup, "duplicate keys %s (goss would silently drop all but the last)" % sorted(dup)
    return loader.construct_mapping(node)


NoDuplicateKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def linux_flavors():
    cfg = Config.load()
    return [(f, cfg.distro_family(f)) for f in cfg.flavors() if cfg.image(f)["os_family"] == "linux"]


def loaded(path, flavor, family):
    return yaml.load(render(path, {"FLAVOR": flavor, "DISTRO_FAMILY": family}), Loader=NoDuplicateKeys)


def test_there_are_linux_flavors_for_both_distro_families():
    assert {fam for _, fam in linux_flavors()} == {"redhat", "debian"}


@pytest.mark.parametrize("flavor,family", linux_flavors())
@pytest.mark.parametrize("path", GOSS_FILES)
def test_goss_tests_have_the_attributes_goss_requires(path, flavor, family):
    for rtype, items in loaded(path, flavor, family).items():
        for name, spec in items.items():
            for attr in REQUIRED.get(rtype, []):
                assert attr in spec, "%s [%s]: %s %r is missing %r (goss: 'Missing required attribute')" % (path, flavor, rtype, name, attr)
            if rtype == "service":
                assert "enabled" in spec or "running" in spec, "%s [%s]: service %r checks nothing" % (path, flavor, name)


@pytest.mark.parametrize("flavor,family", linux_flavors())
def test_compliance_checks_carry_an_id_so_exceptions_can_refer_to_them(flavor, family):
    for rtype, items in loaded("tests/goss/cis.yaml", flavor, family).items():
        for name, spec in items.items():
            assert (spec.get("meta") or {}).get("id", "").startswith("cis-"), "%s %r has no meta.id" % (rtype, name)


@pytest.mark.parametrize("flavor,family", linux_flavors())
@pytest.mark.parametrize("path", GOSS_FILES)
def test_a_regex_pattern_is_wrapped_in_slashes_as_a_whole(path, flavor, family):
    # goss treats a string as a literal unless the WHOLE pattern is wrapped in /.../ (this was wrong once).
    for rtype, items in loaded(path, flavor, family).items():
        for name, spec in items.items():
            for pat in (spec.get("contains") or []) + (spec.get("stdout") or []):
                if any(t in pat.strip("/") for t in ("[0-9]", "(true|false)", ".*")):
                    assert pat.startswith("/") and pat.endswith("/"), "%s %r: regex %r is not wrapped in slashes" % (path, name, pat)


def test_the_expectations_actually_differ_by_distro_family():
    deb, rpm = loaded("tests/goss/linux.yaml", "ubuntu2404", "debian"), loaded("tests/goss/linux.yaml", "rhel9", "redhat")
    assert "auditd" in deb["package"] and "audit" not in deb["package"] and "audit" in rpm["package"]    # package names
    assert "chrony" in deb["service"] and "chronyd" not in deb["service"] and "chronyd" in rpm["service"]    # service names
    assert "sshd" in rpm["service"] and "sshd" not in deb["service"] and "sshd-enabled" in deb["command"]    # ssh.socket on Ubuntu
    assert "apt-get" in deb["command"]["no-pending-security-updates"]["exec"]
    assert "dnf" in rpm["command"]["no-pending-security-updates"]["exec"]
    shadow = {fam: loaded("tests/goss/cis.yaml", f, fam)["file"]["/etc/shadow"] for f, fam in (("ubuntu2404", "debian"), ("rhel9", "redhat"))}
    assert (shadow["debian"]["mode"], shadow["debian"]["group"]) == ("0640", "shadow")    # CIS Ubuntu
    assert (shadow["redhat"]["mode"], shadow["redhat"]["group"]) == ("0000", "root")      # CIS RHEL


def test_the_ssm_agent_check_covers_both_amazon_linux_architectures_and_nothing_else():
    # It used to compare FLAVOR to "al2023", which silently skipped al2023arm.
    has = lambda f, fam: "amazon-ssm-agent" in loaded("tests/goss/linux.yaml", f, fam)["service"]
    assert has("al2023", "redhat") and has("al2023arm", "redhat")
    assert not has("rhel9", "redhat") and not has("ubuntu2404", "debian") and not has("ubuntu2404arm", "debian")
