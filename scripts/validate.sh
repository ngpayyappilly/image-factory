#!/usr/bin/env bash
# Offline validation of every template for EVERY supported platform (including the optional,
# disabled-by-default ones), using vars generated from config/images.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_EC2_METADATA_DISABLED=true PKR_VAR_vsphere_user=validate PKR_VAR_vsphere_password=validate   # real values come from the vault at build time
if=imagefactory; command -v imagefactory >/dev/null || if="python3 -m imagefactory"

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
packer fmt -check -recursive packer

for dir in packer/linux packer/windows; do packer init "$dir" >/dev/null; done

fail=0
while IFS=$'\t' read -r flavor platform; do
  eval "$($if matrix --all --flavor "$flavor" --platform "$platform" --format env)"
  $if packer-vars --flavor "$flavor" --platform "$platform" --version "$flavor-20260101.1" > "$tmp/vars.json"
  if out=$(packer validate -only="$ONLY" -var-file="$tmp/vars.json" "$PACKER_DIR" 2>&1); then
    echo "ok   $flavor/$platform"
  else
    echo "FAIL $flavor/$platform"; echo "$out" | sed 's/^/     /'; fail=1
  fi
done < <($if matrix --all | python3 -c 'import json,sys; [print(r["flavor"],r["platform"],sep="\t") for r in json.load(sys.stdin)]')
exit $fail
