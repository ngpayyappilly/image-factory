"""goss rejects malformed tests at run time ("Syntax Error: Missing required attribute"), which we only see 17+ minutes
into a real build and cannot reproduce locally (goss is Linux-only). Check the rules statically instead."""
import yaml

REQUIRED = {                      # resource type -> attributes goss insists on
    "command": ["exec", "exit-status"],
    "file": ["exists"],
    "package": ["installed"],
    "kernel-param": ["value"],
}


def load(path):
    src = open(path).read()
    # linux.yaml is a Go template; drop the {{ if }} / {{ end }} lines and fill the one value used inline.
    src = "\n".join(l for l in src.splitlines() if "{{ if" not in l and "{{ end" not in l)
    return yaml.safe_load(src.replace("{{ .Env.FLAVOR }}", "al2023"))


def test_goss_tests_have_the_attributes_goss_requires():
    for path in ("tests/goss/linux.yaml", "tests/goss/cis.yaml"):
        doc = load(path)
        for rtype, items in doc.items():
            for name, spec in items.items():
                for attr in REQUIRED.get(rtype, []):
                    assert attr in spec, "%s: %s %r is missing %r (goss: 'Missing required attribute')" % (path, rtype, name, attr)
                if rtype == "service":
                    assert "enabled" in spec or "running" in spec, "%s: service %r checks nothing" % (path, name)


def test_compliance_checks_carry_an_id_so_exceptions_can_refer_to_them():
    for rtype, items in load("tests/goss/cis.yaml").items():
        for name, spec in items.items():
            assert (spec.get("meta") or {}).get("id", "").startswith("cis-"), "%s %r has no meta.id" % (rtype, name)


def test_a_regex_pattern_is_wrapped_in_slashes_as_a_whole():
    # goss treats a string as a literal unless the WHOLE pattern is wrapped in /.../ (this was wrong once).
    for path in ("tests/goss/linux.yaml", "tests/goss/cis.yaml"):
        for rtype, items in load(path).items():
            for name, spec in items.items():
                for pat in (spec.get("contains") or []) + (spec.get("stdout") or []):
                    inner = pat.strip("/")
                    if "[0-9]" in inner or "(true|false)" in inner or ".*" in inner:
                        assert pat.startswith("/") and pat.endswith("/"), "%s %r: regex %r is not wrapped in slashes" % (path, name, pat)
