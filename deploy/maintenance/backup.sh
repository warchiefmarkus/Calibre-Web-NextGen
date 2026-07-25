#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT=/root/calibre/CalibreWeb
MCP=/root/calibre/CalibreMCP
STATE="$ROOT/var/config"
DEST_ROOT="$ROOT/var/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$DEST_ROOT/$STAMP"

install -d -m 0700 "$DEST" "$DEST/cwng" "$DEST/calibremcp" \
  "$DEST/calibre-server" "$DEST/library" "$DEST/systemd"
backup_db() {
  local source=$1 destination=$2
  [[ -f "$source" ]] || return 0
  sqlite3 "$source" "PRAGMA quick_check;" | grep -qx ok
  sqlite3 "$source" ".backup '$destination'"
  chmod 0600 "$destination"
}

for db in app.db cwa.db integration.db; do
  backup_db "$STATE/$db" "$DEST/cwng/$db"
done
install -m 0600 "$STATE/cwng.env" "$DEST/cwng/cwng.env"
install -m 0600 "$MCP/.env" "$DEST/calibremcp/calibremcp.env"
install -m 0600 "$MCP/config/calibremcp.json" "$DEST/calibremcp/calibremcp.json"
backup_db "$MCP/data/rest-idempotency.db" "$DEST/calibremcp/rest-idempotency.db"
backup_db "/root/calibre/Library/metadata.db" "$DEST/library/metadata.db"
backup_db "/root/calibre/CalibreConfig/server-users.sqlite" \
  "$DEST/calibre-server/server-users.sqlite"
cat > "$DEST/library/README.txt" <<'EOF'
This snapshot includes metadata.db but not the physical book files.
Back up /root/calibre/Library book directories separately before disaster recovery.
The RAG index is intentionally omitted because it is rebuildable from the library.
EOF

# Exact code snapshots include tracked modifications and untracked implementation files.
tar -C "$ROOT" -czf "$DEST/cwng/source-tree.tar.gz" \
  --exclude='source/.git' --exclude='source/.venv' \
  --exclude='source/frontend/node_modules' --exclude='source/**/__pycache__' source
tar -C "$ROOT" -czf "$DEST/cwng/control-tree.tar.gz" \
  README.md IMPLEMENTATION_PLAN.md docs deploy UPSTREAM_VERSION
tar -C /root/calibre -czf "$DEST/calibremcp/source-tree.tar.gz" \
  --exclude='CalibreMCP/.git' --exclude='CalibreMCP/.venv' \
  --exclude='CalibreMCP/.env' --exclude='CalibreMCP/data' \
  --exclude='CalibreMCP/run' --exclude='CalibreMCP/logs' \
  --exclude='CalibreMCP/**/__pycache__' CalibreMCP

cp -a "$ROOT/UPSTREAM_VERSION" "$DEST/" 2>/dev/null || true
for unit in \
  calibre-web-nextgen.service calibre-web-backup.service calibre-web-backup.timer \
  calibre-web-reconcile.service calibre-web-reconcile.path calibre-web-reconcile.timer \
  calibremcp.service calibre-server.service calibremcp-rag-status.service \
  calibremcp-rag-worker.service calibremcp-rag-worker.path; do
  systemctl cat "$unit" > "$DEST/systemd/$unit" 2>/dev/null || true
done

git -C "$ROOT/source" status --short > "$DEST/cwng/git-status.txt"
git -C "$ROOT/source" diff --binary > "$DEST/cwng/tracked.patch"
git -C "$ROOT/source" ls-files --others --exclude-standard > "$DEST/cwng/untracked-files.txt"
git -C "$MCP" status --short > "$DEST/calibremcp/git-status.txt"
git -C "$MCP" diff --binary > "$DEST/calibremcp/tracked.patch"
git -C "$MCP" ls-files --others --exclude-standard > "$DEST/calibremcp/untracked-files.txt"

(cd "$DEST" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
chmod -R go-rwx "$DEST"
find "$DEST_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf -- {} +
echo "$DEST"
