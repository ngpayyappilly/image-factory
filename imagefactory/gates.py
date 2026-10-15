"""Turns raw scanner/test output into pass/fail gate results.

Inputs are produced inside the build VM (see packer/scripts/linux/): goss.json (functional
tests), compliance.json (CIS audit, goss format), trivy.json (vulnerabilities + secrets),
sbom.json (CycloneDX or SPDX).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, Iterable, Tuple

Result = Tuple[str, str]  # (status, detail)
BLOCKING_SEVERITIES = ("CRITICAL", "HIGH")


def _load(path: Path):
    return json.loads(Path(path).read_text())


def evaluate_goss(report: dict) -> Result:
    s = report.get("summary", {})
    failed, total = s.get("failed-count", 0), s.get("test-count", 0)
    if total == 0:
        return "fail", "no tests ran"
    if failed:
        names = [r.get("title") or r.get("resource-id") for r in report.get("results", []) if not r.get("successful")]
        return "fail", "%d/%d failed: %s" % (failed, total, ", ".join(map(str, names[:10])))
    return "pass", "%d tests passed" % total


def evaluate_compliance(report: dict, min_score: float, excepted: Iterable[str] = ()) -> Result:
    excepted = set(excepted)
    results = report.get("results", [])
    if not results:
        return "fail", "no compliance checks ran"
    counted = failed = 0
    for r in results:
        ident = (r.get("meta") or {}).get("id") or r.get("title")
        if ident in excepted:
            continue
        counted += 1
        failed += 0 if r.get("successful") else 1
    score = (counted - failed) / counted if counted else 0.0
    detail = "score %.3f (%d/%d), threshold %.3f" % (score, counted - failed, counted, min_score)
    return ("pass" if score >= min_score else "fail"), detail


def _trivy_items(report: dict, key: str):
    for res in report.get("Results") or []:
        for item in res.get(key) or []:
            yield res.get("Target", ""), item


def evaluate_vulns(report: dict, excepted: Iterable[str] = ()) -> Result:
    excepted = set(excepted)
    bad = [i for _, i in _trivy_items(report, "Vulnerabilities")
           if i.get("Severity") in BLOCKING_SEVERITIES and i.get("VulnerabilityID") not in excepted]
    if bad:
        ids = sorted({"%s(%s)" % (i["VulnerabilityID"], i["Severity"]) for i in bad})
        return "fail", "%d critical/high findings: %s" % (len(bad), ", ".join(ids[:10]))
    return "pass", "no critical/high findings"


def evaluate_secrets(report: dict, excepted: Iterable[str] = ()) -> Result:
    excepted = set(excepted)
    bad = [(t, i) for t, i in _trivy_items(report, "Secrets") if i.get("RuleID") not in excepted]
    if bad:
        return "fail", "%d secrets found: %s" % (len(bad), ", ".join(sorted({"%s in %s" % (i.get("RuleID"), t) for t, i in bad})[:5]))
    return "pass", "no secrets found"


def evaluate_sbom(report: dict) -> Result:
    if report.get("bomFormat") == "CycloneDX":
        n = len(report.get("components") or [])
    elif str(report.get("spdxVersion", "")).startswith("SPDX"):
        n = len(report.get("packages") or [])
    else:
        return "fail", "unrecognised SBOM format"
    return ("pass", "%d components" % n) if n else ("fail", "SBOM has no components")


def ingest(report_dir: Path, cfg) -> Dict[str, Result]:
    """Evaluate every report present; absent reports are simply not recorded (so the gate
    stays unmet and promotion is blocked)."""
    d = Path(report_dir)
    min_score = float(cfg.policy["compliance_min_score"])
    plan: Dict[str, Callable[[dict], Result]] = {
        "goss": evaluate_goss,
        "compliance": lambda r: evaluate_compliance(r, min_score, cfg.active_exceptions("compliance")),
        "vuln_scan": lambda r: evaluate_vulns(r, cfg.active_exceptions("vuln_scan")),
        "secrets": lambda r: evaluate_secrets(r, cfg.active_exceptions("secrets")),
        "sbom": evaluate_sbom,
    }
    files = {"goss": "goss.json", "compliance": "compliance.json", "vuln_scan": "trivy.json",
             "secrets": "trivy.json", "sbom": "sbom.json"}
    out = {}
    for gate, fn in plan.items():
        p = d / files[gate]
        if not p.exists():
            continue
        try:
            out[gate] = fn(_load(p))
        except (ValueError, KeyError) as e:  # unparseable output must fail, not pass
            out[gate] = ("fail", "could not parse %s: %s" % (p.name, e))
    return out
