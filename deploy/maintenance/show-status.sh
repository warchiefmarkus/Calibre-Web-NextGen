#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/calibre/CalibreWeb
printf '%s\n' '=== Services ==='
systemctl --no-pager --full status calibre-web-nextgen nginx \
  calibre-server calibremcp calibremcp-rag-status.service || true
printf '%s\n' '=== Ports ==='
ss -lntp | grep -E ':8080|:8083|:18083|:10720|:10721' || true
printf '%s\n' '=== Health ==='
curl -fsS http://127.0.0.1:18083/healthz; echo
curl -fsS http://127.0.0.1:8083/healthz; echo
printf '%s\n' '=== Library ==='
sqlite3 /root/calibre/Library/metadata.db \
  "select 'books', count(*) from books; select 'cwng_checksum_tables', count(*) from sqlite_master where name='book_format_checksums';"
printf '%s\n' '=== CWNG state ==='
du -sh "$ROOT/source" "$ROOT/var"
git -C "$ROOT/source" status --short --branch
