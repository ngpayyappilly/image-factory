#!/usr/bin/env bash
# Build ONE (flavor, platform) image from the pipeline or a workstation, then record it.
#
#   scripts/build.sh <flavor> <platform> <version>
#
# <version> is computed once per pipeline run (`imagefactory version-next`) and shared by every
# platform build of that flavor, so all platforms carry the identical version string.
#
# Env:
#   IMAGEFACTORY_AUTHOR   who triggered the build (the promotion approver must be someone else)
#   IMAGEFACTORY_RUN_ID   pipeline run id
#   TOOL_URLS_JSON / TOOL_SHA256_JSON   pinned goss/trivy/syft locations (JSON maps), see README
set -euo pipefail
cd "$(dirname "$0")/.."

flavor=${1:?flavor}; platform=${2:?platform}; version=${3:?version}
author=${IMAGEFACTORY_AUTHOR:-$(git config user.email 2>/dev/null || echo unknown)}
run_id=${IMAGEFACTORY_RUN_ID:-local-$(date +%s)}
commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)
if=imagefactory; command -v imagefactory >/dev/null || if="python3 -m imagefactory"

eval "$($if matrix --flavor "$flavor" --platform "$platform" --format env)"   # OS_FAMILY PACKER_DIR ONLY SOURCE_TYPE

out=artifacts/$version
mkdir -p "$out"
$if packer-vars --flavor "$flavor" --platform "$platform" --version "$version" --commit "$commit" > "$out/$platform.pkrvars.json"

extra=()
[ -n "${TOOL_URLS_JSON:-}" ]   && extra+=(-var "tool_urls=$TOOL_URLS_JSON")
[ -n "${TOOL_SHA256_JSON:-}" ] && extra+=(-var "tool_sha256=$TOOL_SHA256_JSON")

# Roles: external CIS roles are pinned in ansible/requirements.yml
if [ "$OS_FAMILY" = linux ] && [ ! -d ansible/external ]; then
  ansible-galaxy install -r ansible/requirements.yml -p ansible/external
fi

packer init "$PACKER_DIR"
packer build -only="$ONLY" \
  -var-file="$out/$platform.pkrvars.json" \
  -var "manifest_path=$out/packer-manifest-$platform.json" \
  -var "report_dir=artifacts/reports" \
  ${extra[@]+"${extra[@]}"} \
  "$PACKER_DIR"

$if manifest-create --flavor "$flavor" --version "$version" --platform "$platform" \
  --packer-manifest "$out/packer-manifest-$platform.json" --commit "$commit" --run-id "$run_id" --author "$author"

# Keep the evidence even (especially) when a gate fails.
$if evidence-upload --flavor "$flavor" --version "$version" --reports "artifacts/reports/$version" || echo "warning: evidence upload failed" >&2

# Evidence -> gates. Non-zero when a gate fails; the pipeline decides what to do with a failed image
# (it is still recorded, and cannot be promoted past dev).
set +e
$if gate-ingest --flavor "$flavor" --version "$version" --platform "$platform" \
  --reports "artifacts/reports/$version/$SOURCE_TYPE"
rc=$?
set -e
exit $rc
