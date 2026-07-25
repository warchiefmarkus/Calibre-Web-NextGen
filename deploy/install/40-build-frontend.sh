#!/usr/bin/env bash
set -euo pipefail

FRONTEND=/root/calibre/CalibreWeb/source/frontend
cd "$FRONTEND"
npm ci
npm run build
rm -rf node_modules

test -f ../cps/static/app/index.html
find ../cps/static/app -maxdepth 2 -type f -printf '%P\n' | sort | head -30
echo "Frontend built; node_modules removed from production tree"
