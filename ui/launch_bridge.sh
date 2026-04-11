#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

exec "$PYTHON_BIN" "$ROOT_DIR/ui/browser_bridge.py" \
  --browser firefox \
  --persistent-profile \
  --user-data-dir "$ROOT_DIR/.pw-user-data-firefox" \
  --url https://papergames.io/en/connect4 \
  --mode auto \
  --player auto \
  --weak \
  --poll-ms 250 \
  --post-game-wait-sec 5 \
  --post-game-reload-sec 0 \
  "$@"
