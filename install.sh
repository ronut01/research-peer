#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -d "$SCRIPT_DIR/node_modules/@modelcontextprotocol/sdk" ]; then
  command -v npm >/dev/null 2>&1 || {
    echo "Research Peer requires Node.js/npm to install its Claude Channel dependency." >&2
    exit 1
  }
  npm --prefix "$SCRIPT_DIR" ci --omit=dev --ignore-scripts
fi
PYTHONPATH="$SCRIPT_DIR/src" exec python3 -m research_peer.installer install --source "$SCRIPT_DIR"
