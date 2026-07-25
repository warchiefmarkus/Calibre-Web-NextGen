#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  build-essential pkg-config python3-dev python3-venv \
  libldap2-dev libsasl2-dev libssl-dev libffi-dev \
  libxml2-dev libxslt1-dev zlib1g-dev libjpeg-dev \
  libmagic1 libmagic-dev libmagickwand-dev \
  libsqlite3-dev sqlite3 acl curl ca-certificates \
  unrar-free

apt-get clean
rm -rf /var/lib/apt/lists/*
echo "System dependencies installed"
