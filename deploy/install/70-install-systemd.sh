#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/calibre/CalibreWeb
for unit in \
  calibre-web-nextgen.service \
  calibre-web-backup.service calibre-web-backup.timer \
  calibre-web-reconcile.service calibre-web-reconcile.path calibre-web-reconcile.timer; do
  install -m 0644 "$ROOT/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now calibre-web-nextgen.service
systemctl enable --now calibre-web-backup.timer
systemctl enable --now calibre-web-reconcile.path calibre-web-reconcile.timer
echo "Systemd service, backup, and reconcile units installed and started"
