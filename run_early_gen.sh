#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$ROOT/.venv/bin/python" "$ROOT/tools/generate_early_game_dataset.py" \
  --solver "$ROOT/build/solver" \
  --out "$ROOT/data/early_game.csv" \
  --rows 500 \
  --seed 42 \
  --verbose \
  --append
