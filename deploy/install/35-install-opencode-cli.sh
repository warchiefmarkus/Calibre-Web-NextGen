#!/usr/bin/env bash
set -euo pipefail

VERSION="${OPENCODE_CLI_VERSION:-1.18.31}"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to install the pinned OpenCode CLI ($VERSION)." >&2
  exit 1
fi

npm install -g "opencode-ai@${VERSION}"
command -v opencode >/dev/null
INSTALLED="$(opencode --version)"
echo "OpenCode CLI installed: ${INSTALLED}"
