#!/usr/bin/env bash
# Last provisioner. Removes the build tooling/evidence and the build user's SSH key so
# nothing used during the build ships in the image.
set -euo pipefail

rm -rf /tmp/imagefactory-tools /tmp/imagefactory-reports /tmp/imagefactory-goss
rm -f /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys
rm -f /root/.bash_history /home/*/.bash_history
find /var/log -type f -exec truncate -s 0 {} \; 2>/dev/null || true
sync
