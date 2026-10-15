#!/usr/bin/env bash
# Produces the evidence the promotion gates are decided from. It does NOT decide pass/fail:
# `imagefactory gate-ingest` does that from the downloaded reports, so failures stay traceable.
#
#   goss.json        functional tests              (tests/goss/linux.yaml)
#   compliance.json  CIS Level 1 subset audit      (tests/goss/cis.yaml)
#   trivy.json       vulnerabilities + secrets
#   sbom.json        CycloneDX SBOM
#
# Tools are downloaded from pinned URLs and verified against pinned SHA256s. Missing or
# unverifiable tooling exits 1 (evidence incomplete), which leaves the gates unmet.
set -uo pipefail

R=/tmp/imagefactory-reports
T=/tmp/imagefactory-tools
G=/tmp/imagefactory-goss
mkdir -p "$R" "$T"
rc=0

fetch() {  # name url sha256
  local name=$1 url=$2 sha=$3
  if [ -z "$url" ] || [ -z "$sha" ]; then echo "gates: no pinned URL/SHA256 for $name" >&2; return 1; fi
  curl -fsSL --retry 3 "$url" -o "$T/$name.dl" || return 1
  echo "$sha  $T/$name.dl" | sha256sum -c - >&2 || { echo "gates: checksum mismatch for $name" >&2; return 1; }
  case "$url" in
    *.tar.gz|*.tgz) tar -xzf "$T/$name.dl" -C "$T" "$name" ;;
    *) mv "$T/$name.dl" "$T/$name" ;;
  esac
  chmod +x "$T/$name"
}

fetch goss  "${GOSS_URL:-}"  "${GOSS_SHA256:-}"  || rc=1
fetch trivy "${TRIVY_URL:-}" "${TRIVY_SHA256:-}" || rc=1
fetch syft  "${SYFT_URL:-}"  "${SYFT_SHA256:-}"  || rc=1

# cleanup.sh removed the real host keys and sshd refuses to run (even -T) without one, so the
# sshd checks use a throwaway key. It lives in $T, which the scans skip and seal.sh deletes.
ssh-keygen -q -t ed25519 -N '' -f "$T/hostkey"

# goss exits non-zero on failing tests; we want the report either way.
if [ -x "$T/goss" ]; then
  FLAVOR="${FLAVOR:-}" "$T/goss" --gossfile "$G/linux.yaml" validate --format json > "$R/goss.json" || true
  FLAVOR="${FLAVOR:-}" "$T/goss" --gossfile "$G/cis.yaml"   validate --format json > "$R/compliance.json" || true
fi

SKIP="/proc,/sys,/dev,/run,$T,$R,$G"
if [ -x "$T/trivy" ]; then
  # TRIVY_DB_REPOSITORY / TRIVY_JAVA_DB_REPOSITORY should point at the internal mirror for hermetic builds.
  "$T/trivy" rootfs / --scanners vuln,secret --ignore-unfixed --skip-dirs "$SKIP" \
    --format json --output "$R/trivy.json" --quiet || rc=1
fi

if [ -x "$T/syft" ]; then
  "$T/syft" dir:/ -o cyclonedx-json="$R/sbom.json" \
    --exclude './proc/**' --exclude './sys/**' --exclude './dev/**' --exclude './run/**' --exclude './tmp/**' || rc=1
fi

{ "$T/goss" --version; "$T/trivy" --version; "$T/syft" --version; } > "$R/tool-versions.txt" 2>&1 || true
exit $rc
