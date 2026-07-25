#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/calibre/CalibreWeb
VENV="$ROOT/source/.venv"
rm -rf "$VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel
LOCK="$ROOT/source/requirements.lock.txt"
[[ -f "$LOCK" ]] || { echo "Missing pinned requirements.lock.txt" >&2; exit 1; }
"$VENV/bin/pip" install -r "$LOCK"
echo "Python environment ready from pinned lockfile: $VENV"
