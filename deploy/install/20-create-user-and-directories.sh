#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/calibre/CalibreWeb
if ! id calibreweb >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/calibreweb \
    --create-home --shell /usr/sbin/nologin calibreweb
fi
getent group calibremutate >/dev/null || groupadd --system calibremutate
usermod -a -G calibremutate calibreweb
if id calibremcp >/dev/null 2>&1; then
  usermod -a -G calibremutate calibremcp
else
  echo "Missing required CalibreMCP service user: calibremcp" >&2
  exit 1
fi

mkdir -p "$ROOT"/var/{config,cache,staging/uploads,staging/covers,tmp/conversion,logs,run,backups}
chown -R calibreweb:calibreweb \
  "$ROOT/var/config" "$ROOT/var/cache" "$ROOT/var/tmp" \
  "$ROOT/var/logs" "$ROOT/var/run"
chown -R calibreweb:calibremutate "$ROOT/var/staging"
chown root:root "$ROOT/var/backups"
chmod 700 "$ROOT/var/config" "$ROOT/var/backups"
chmod 2750 "$ROOT/var/staging" "$ROOT/var/staging/uploads" "$ROOT/var/staging/covers"

setfacl -m u:calibreweb:--x /root
setfacl -m u:calibreweb:--x /root/calibre
setfacl -R -m u:calibreweb:rX /root/calibre/Library
setfacl -R -m u:calibreweb:rX "$ROOT/source"
setfacl -R -d -m u:calibreweb:rX /root/calibre/Library

[[ -f "$ROOT/var/config/cwng.env" ]] || {
  echo "Create var/config/cwng.env from deploy/env/cwng.env.example first" >&2
  exit 1
}
chmod 600 "$ROOT/var/config/cwng.env"
chown calibreweb:calibreweb "$ROOT/var/config/cwng.env"
echo "Users, shared staging, and read-only library ACL configured"
