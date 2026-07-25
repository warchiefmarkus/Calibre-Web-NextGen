#!/usr/bin/env bash
set -euo pipefail

BASE=http://127.0.0.1:18083
systemctl is-active --quiet calibre-web-nextgen.service
curl -fsS "$BASE/healthz" | grep -q 'mcp-managed-library'

if runuser -u calibreweb -- touch /root/calibre/Library/.cwng-write-test 2>/dev/null; then
  rm -f /root/calibre/Library/.cwng-write-test
  echo "ERROR: calibreweb can write to library" >&2
  exit 1
fi

if sqlite3 /root/calibre/Library/metadata.db \
  "SELECT 1 FROM sqlite_master WHERE type='table' AND name='book_format_checksums';" | grep -q 1; then
  echo "ERROR: CWNG checksum table exists in metadata.db" >&2
  exit 1
fi

MAIN_PID="$(systemctl show -p MainPID --value calibre-web-nextgen.service)"
DESCENDANTS="$(pgrep -P "$MAIN_PID" -a 2>/dev/null || true)"
if printf '%s\n' "$DESCENDANTS" | grep -Eq 'node|vite|s6-svscan'; then
  echo "ERROR: unexpected CWNG helper process" >&2
  printf '%s\n' "$DESCENDANTS" >&2
  exit 1
fi

ss -lntp | grep -q '127.0.0.1:18083'
ss -lntp | grep -q '127.0.0.1:10720'
if ss -lnt | grep -qE '(^|[[:space:]])[^[:space:]]*:10721[[:space:]]'; then
  echo "ERROR: legacy unauthenticated RAG LAN port 10721 is listening" >&2
  exit 1
fi
curl -fsS http://127.0.0.1:10720/readyz | grep -q '"writer":"ready"'
[[ -L /root/calibre/CalibreWeb/deploy ]]
[[ "$(readlink /root/calibre/CalibreWeb/deploy)" == "source/deploy" ]]
if command -v nginx >/dev/null 2>&1; then
  curl -fsS http://127.0.0.1:8083/healthz | grep -q 'mcp-managed-library'
  curl -fsSI http://127.0.0.1:8083/ | grep -qi '^location: .*\/app\/'
  curl -fsS http://127.0.0.1:8083/app/ | grep -qi '<!doctype html>'
  curl -fsS http://192.168.31.150:8080/ >/dev/null
fi

grep -q '^CWNG_NATIVE_READER_DATA=true$' \
  /root/calibre/CalibreWeb/var/config/cwng.env
/root/calibre/CalibreWeb/deploy/tests/validate-managed-contract.py
/root/calibre/CalibreWeb/deploy/migrations/migrate-legacy-reader-data.py \
  | grep -q '"mode": "dry-run"'

echo "CWNG validation passed"
