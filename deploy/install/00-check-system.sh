#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/calibre/CalibreWeb
for cmd in git python3 node npm sqlite3 systemctl; do
  command -v "$cmd" >/dev/null || { echo "Missing command: $cmd" >&2; exit 1; }
done

[ -f /root/calibre/Library/metadata.db ] || {
  echo "Missing Calibre library metadata.db" >&2
  exit 1
}
[ -d "$ROOT/source/.git" ] || {
  echo "Missing CWNG source checkout" >&2
  exit 1
}

echo "Python: $(python3 --version)"
echo "Node: $(node --version)"
echo "npm: $(npm --version)"
echo "Calibre: $(/usr/bin/calibre-server --version | head -1)"
echo "CWNG: $(git -C "$ROOT/source" rev-parse HEAD)"
echo "Preflight OK"
