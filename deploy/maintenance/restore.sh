#!/usr/bin/env bash
set -euo pipefail
umask 077

backup=${1:-}
mode=${2:-validate}
ROOT=/root/calibre/CalibreWeb
MCP=/root/calibre/CalibreMCP
FULL=false
[[ "$mode" == apply-full || "$mode" == apply ]] && FULL=true

[[ -d "$backup" ]] || {
  echo "Usage: $0 BACKUP_DIR [validate|apply-state|apply-full|apply]" >&2
  exit 2
}
[[ "$mode" =~ ^(validate|apply-state|apply-full|apply)$ ]] || {
  echo "Invalid restore mode" >&2
  exit 2
}

cd "$backup"
sha256sum -c SHA256SUMS
find . -name '*.db' -print0 | while IFS= read -r -d '' db; do
  sqlite3 "$db" 'PRAGMA integrity_check;' | grep -qx ok
done

for required in cwng/cwng.env calibremcp/calibremcp.env calibremcp/calibremcp.json; do
  [[ -f "$required" ]] || { echo "Missing backup item: $required" >&2; exit 4; }
done

if $FULL; then
  command -v rsync >/dev/null || { echo "rsync is required for full restore" >&2; exit 4; }
  for archive in cwng/source-tree.tar.gz cwng/control-tree.tar.gz calibremcp/source-tree.tar.gz; do
    [[ -f "$archive" ]] || { echo "Missing source archive: $archive" >&2; exit 4; }
    tar -tzf "$archive" >/dev/null
  done
fi

[[ "$mode" == validate ]] && { echo "Backup validation passed"; exit 0; }

for unit in calibre-web-nextgen.service calibremcp.service calibre-server.service \
            calibremcp-rag-status.service; do
  if systemctl is-active --quiet "$unit"; then
    echo "Refusing live restore: stop $unit first" >&2
    exit 3
  fi
done

pre="$ROOT/var/pre-restore-$(date +%Y%m%d-%H%M%S)"
install -d -m 0700 "$pre"
cp -a "$ROOT/var/config" "$pre/cwng-config"
cp -a "$MCP/.env" "$MCP/config" "$pre/"
[[ -f "$MCP/data/rest-idempotency.db" ]] && \
  cp -a "$MCP/data/rest-idempotency.db" "$pre/"
[[ -f /root/calibre/CalibreConfig/server-users.sqlite ]] && \
  cp -a /root/calibre/CalibreConfig/server-users.sqlite "$pre/"

tar -C "$ROOT" -czf "$pre/cwng-source.tar.gz" \
  --exclude='source/.git' --exclude='source/.venv' \
  --exclude='source/frontend/node_modules' source
tar -C "$ROOT" -czf "$pre/cwng-control.tar.gz" \
  README.md IMPLEMENTATION_PLAN.md docs deploy UPSTREAM_VERSION
tar -C /root/calibre -czf "$pre/calibremcp-source.tar.gz" \
  --exclude='CalibreMCP/.git' --exclude='CalibreMCP/.venv' \
  --exclude='CalibreMCP/.env' --exclude='CalibreMCP/data' CalibreMCP

for db in app.db cwa.db integration.db; do
  [[ -f "cwng/$db" ]] && install -o calibreweb -g calibreweb -m 0600 \
    "cwng/$db" "$ROOT/var/config/$db"
done
install -o calibreweb -g calibreweb -m 0600 \
  cwng/cwng.env "$ROOT/var/config/cwng.env"
install -o root -g calibremcp -m 0640 \
  calibremcp/calibremcp.env "$MCP/.env"
install -o root -g calibremcp -m 0640 \
  calibremcp/calibremcp.json "$MCP/config/calibremcp.json"

if [[ -f calibremcp/rest-idempotency.db ]]; then
  install -d -o calibremcp -g calibremcp -m 0700 "$MCP/data"
  install -o calibremcp -g calibremcp -m 0600 \
    calibremcp/rest-idempotency.db "$MCP/data/rest-idempotency.db"
fi
if [[ -f calibre-server/server-users.sqlite ]]; then
  install -o root -g root -m 0600 calibre-server/server-users.sqlite \
    /root/calibre/CalibreConfig/server-users.sqlite
fi

if $FULL; then
  tmp=$(mktemp -d "$ROOT/var/tmp/restore.XXXXXX")
  trap 'rm -rf "$tmp"' EXIT
  tar -xzf cwng/source-tree.tar.gz -C "$tmp"
  rsync -a --delete --exclude=.git --exclude=.venv \
    --exclude=frontend/node_modules "$tmp/source/" "$ROOT/source/"
  rm -rf "$tmp/source"
  tar -xzf cwng/control-tree.tar.gz -C "$ROOT"
  tar -xzf calibremcp/source-tree.tar.gz -C "$tmp"
  rsync -a --delete --exclude=.git --exclude=.venv --exclude=.env \
    --exclude=data --exclude=run --exclude=logs \
    "$tmp/CalibreMCP/" "$MCP/"

  install -m 0644 "$MCP/calibre-server.service" \
    /etc/systemd/system/calibre-server.service
  install -m 0644 "$MCP/calibremcp.service" \
    /etc/systemd/system/calibremcp.service
  for unit in calibremcp-rag-status.service calibremcp-rag-sync.service \
              calibremcp-rag-sync.timer calibremcp-rag-worker.service \
              calibremcp-rag-worker.path; do
    [[ -f "$MCP/deploy/systemd/$unit" ]] && \
      install -m 0644 "$MCP/deploy/systemd/$unit" "/etc/systemd/system/$unit"
  done
  systemctl daemon-reload
  bash "$ROOT/deploy/install/20-create-user-and-directories.sh"
  bash "$ROOT/deploy/install/30-create-python-venv.sh"
  bash "$ROOT/deploy/install/40-build-frontend.sh"
  "$MCP/.venv/bin/python" -m compileall -q "$MCP/src"
  systemctl start calibre-server.service calibremcp.service \
    calibremcp-rag-status.service
  bash "$ROOT/deploy/install/70-install-systemd.sh"
  bash "$ROOT/deploy/install/80-install-nginx.sh"
  bash "$ROOT/deploy/install/90-run-validation.sh"
else
  echo "State restored. Start calibre-server, calibremcp and CWNG services manually."
fi

echo "Restore applied. Previous state: $pre"
