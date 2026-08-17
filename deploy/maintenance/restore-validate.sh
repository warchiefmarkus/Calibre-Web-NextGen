#!/usr/bin/env bash
set -euo pipefail
archive=${1:-}
[[ -n "$archive" && -f "$archive" ]] || { echo "Usage: $0 BACKUP.tar.gz" >&2; exit 2; }
for unit in calibre-web-nextgen.service calibremcp.service calibre-server.service; do
  if systemctl is-active --quiet "$unit"; then
    echo "Refusing live restore: stop $unit first" >&2; exit 3
  fi
done
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
tar -xzf "$archive" -C "$tmp"
find "$tmp" -name '*.db' -print0 | while IFS= read -r -d '' db; do
  sqlite3 "$db" 'PRAGMA integrity_check;' | grep -qx ok
done
echo "Backup validated in $tmp. Copy is intentionally manual; no production files were overwritten."
