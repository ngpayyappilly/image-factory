#!/usr/bin/env bash
# Install pinned, checksum-verified build tools (Packer, cosign) on a Linux agent or runner that does not
# already have them. GitHub-hosted runners use pinned Actions instead; Azure DevOps agents and
# self-hosted runners use this.
#
#   scripts/install-tools.sh packer cosign
#
# Installs into $TOOLS_DIR (default ./.tools/bin) and prints a line to add it to PATH for the CI system.
# To bump a version: change the pin AND its checksums below. Checksums come from the publisher's files:
#   packer: https://releases.hashicorp.com/packer/<ver>/packer_<ver>_SHA256SUMS
#   cosign: https://github.com/sigstore/cosign/releases/download/<ver>/cosign_checksums.txt
set -euo pipefail

PACKER_VERSION=1.16.1
PACKER_SHA256_amd64=af38a9e93e4ed1b9ca68206ae969c64c300c82a3dde46a780dfa629f0867f651
PACKER_SHA256_arm64=4784ac0b9228a61f3ecb3861dbf0bf9ebeab6ddb0f5a10466126b8daa5db0de5

COSIGN_VERSION=v2.6.5
COSIGN_SHA256_amd64=c3b4f5410e608af03a5eb0aaac84a4313d8da131248e08ff1759ac70c79d1644
COSIGN_SHA256_arm64=426193b4c5da4d4d643e822f48fe0cc8a476ca1782a272704831f5a0cef716d7

[ "$(uname -s)" = Linux ] || { echo "install-tools.sh supports Linux only" >&2; exit 1; }
case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) echo "unsupported CPU architecture: $(uname -m)" >&2; exit 1 ;;
esac

dest=${TOOLS_DIR:-$PWD/.tools/bin}
mkdir -p "$dest"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

sha256_of() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

verify() {  # file expected
  local got; got=$(sha256_of "$1")
  [ "$got" = "$2" ] || { echo "checksum mismatch for $(basename "$1"): expected $2, got $got" >&2; exit 1; }
}

install_packer() {
  if [ -x "$dest/packer" ] && [ "$("$dest/packer" version | head -1)" = "Packer v$PACKER_VERSION" ]; then return; fi
  local want="PACKER_SHA256_$arch"
  curl -fsSL --retry 3 -o "$tmp/packer.zip" "https://releases.hashicorp.com/packer/$PACKER_VERSION/packer_${PACKER_VERSION}_linux_${arch}.zip"
  verify "$tmp/packer.zip" "${!want}"
  python3 -c "import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extract('packer', sys.argv[2])" "$tmp/packer.zip" "$dest"
  chmod +x "$dest/packer"
  echo "installed packer $PACKER_VERSION" >&2
}

install_cosign() {
  if [ -x "$dest/cosign" ] && "$dest/cosign" version 2>/dev/null | grep -q "GitVersion:.*$COSIGN_VERSION"; then return; fi
  local want="COSIGN_SHA256_$arch"
  curl -fsSL --retry 3 -o "$tmp/cosign" "https://github.com/sigstore/cosign/releases/download/$COSIGN_VERSION/cosign-linux-$arch"
  verify "$tmp/cosign" "${!want}"
  install -m 0755 "$tmp/cosign" "$dest/cosign"
  echo "installed cosign $COSIGN_VERSION" >&2
}

[ $# -gt 0 ] || { echo "usage: install-tools.sh packer|cosign ..." >&2; exit 2; }
for t in "$@"; do
  case "$t" in
    packer) install_packer ;;
    cosign) install_cosign ;;
    *) echo "unknown tool: $t" >&2; exit 2 ;;
  esac
done

# Hand the directory to the CI system.
if [ -n "${TF_BUILD:-}" ]; then echo "##vso[task.prependpath]$dest"; fi                 # Azure DevOps
if [ -n "${GITHUB_PATH:-}" ]; then echo "$dest" >> "$GITHUB_PATH"; fi                    # GitHub Actions
echo "tools in $dest (add to PATH)"
