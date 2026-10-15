#!/usr/bin/env bash
# Strip machine identity so every instance launched from the image is unique.
# Runs BEFORE tests/scans so they evaluate exactly what ships. The build user's SSH key is
# deliberately left for seal.sh: Packer still needs it for the remaining provisioners.
set -euo pipefail

# Host keys are private keys: they must not ship, and the secret scan would (rightly) flag them.
# The running sshd keeps its keys in memory, so Packer's next connection still works.
rm -f /etc/ssh/ssh_host_*

: > /etc/machine-id
rm -f /var/lib/dbus/machine-id

# cloud-init runs again on first boot of every instance (users, keys, hostname, user-data).
cloud-init clean --logs --seed 2>/dev/null || cloud-init clean --logs 2>/dev/null || true

if command -v dnf >/dev/null 2>&1; then dnf clean all; rm -rf /var/cache/dnf/*; fi
if command -v apt-get >/dev/null 2>&1; then apt-get clean; rm -rf /var/lib/apt/lists/*; fi

rm -rf /var/tmp/* /root/.cache
find /var/log -type f -exec truncate -s 0 {} \; 2>/dev/null || true
rm -f /root/.bash_history /home/*/.bash_history
sync
