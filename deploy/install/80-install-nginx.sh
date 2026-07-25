#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/calibre/CalibreWeb
export DEBIAN_FRONTEND=noninteractive
if ! command -v nginx >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends nginx-light
  apt-get clean
  rm -rf /var/lib/apt/lists/*
fi

install -m 0644 "$ROOT/deploy/nginx/calibre-web-nextgen.conf" \
  /etc/nginx/conf.d/calibre-web-nextgen.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx.service
systemctl restart nginx.service
echo "Nginx installed: LAN :8083 -> CWNG and 192.168.31.150:8080 -> original calibre-server"
