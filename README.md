# Connect4 Solver + UI

This repository contains:
- A C++ Connect4 solver in `src/`
- Opening-book data and benchmark output in `data/`
- Solver/backfill scripts in `tools/`
- Benchmark suites in `suites/`
- Python/Pygame UI in `ui/`

## Project Layout

- `src/` C++ solver sources and headers
- `tools/` book population/backfill scripts and utilities
- `data/opening_book.txt` opening book (`sequence best_move score`)
- `ui/` Python game frontends (`connect4.py`, `connect4vsAI.py`)
- `.vscode/` launch/settings for VS Code

## Prerequisites

- Linux/macOS shell
- `g++` with C++17 support
- Python 3.10+
- `timeout` command (GNU coreutils)

## Quick Start

```bash
make all
make venv
make ui-deps
```

Then run either UI:

```bash
make ui-connect4
# or
make ui-vsai
```

## Browser Bridge (Experimental)

The bridge (`ui/browser_bridge.py`) connects the local solver to a live browser game.

Install dependencies once:

```bash
make ui-deps
make playwright-install
```

Recommended observe-mode command (papergames + persistent Firefox profile):

```bash
.venv/bin/python ui/browser_bridge.py \
  --site-mode papergames \
  --browser firefox \
  --persistent-profile \
  --user-data-dir .pw-user-data-firefox \
  --url https://papergames.io/en/connect4 \
  --mode observe \
  --player auto \
  --window-width 1920 \
  --window-height 1200
```

Modes:
- `--mode observe`: print sequence + suggested move only (no clicks)
- `--mode assist`: ask before clicking
- `--mode auto`: click automatically

Key options:
- `--player 1|2|auto`: choose your side (or prompt at runtime with `auto`)
- `--weak`: use weak solver mode (`./solver -w`)
- `--auto-rematch`: in auto mode, click Rematch after terminal state when available (default behavior is leave room)
- `--post-game-wait-sec N`: in auto papergames mode, wait `N` seconds on terminal page before leave/rematch actions (default `5`)
- `--post-game-reload-sec N`: in auto papergames mode, reload lobby if post-game controls do not appear within `N` seconds (`0` disables, default)
- `--manual-fallback --manual-input-mode incremental|full`: fallback input when board parsing fails
- `--block-ads --block-level conservative|aggressive`: optional request blocking
- `--config ui/browser_targets.papergames.json`: custom board selector config

Current papergames behavior:
- Uses papergames-specific parsing with grid column-count deltas (`source=grid-delta`) to track moves robustly.
- Uses solver status endpoint (`sequence!`) to validate snapshots and detect `win1|win2|draw|invalid`.
- In auto mode, after terminal state it waits `--post-game-wait-sec` (default `5s`) before leave/rematch actions.
- If post-game controls are delayed or missing, auto mode can optionally fall back to reloading `https://papergames.io/en/connect4` after the configured timeout and resumes matchmaking.
- If opponent leaves/disconnects, auto mode leaves room and starts new matchmaking.
- After leaving room in auto mode, it clicks `Play online` with random player and continues.
- If the lobby remains idle after a queue click, auto mode retries queue controls automatically.
- If a game abort/disconnect causes sequence loss, auto mode recovers via terminal/post-game/lobby detection and resumes lifecycle flow.
- Suppresses duplicate suggestion spam and prints opponent move lines when detected.
- Records slow exact solves (`>10s`) to `data/slow_solve_prefixes.log` (one entry per sequence per run) for targeted opening-book expansion.

Recommended auto-mode command:

```bash
.venv/bin/python ui/browser_bridge.py \
  --site-mode papergames \
  --browser firefox \
  --persistent-profile \
  --user-data-dir .pw-user-data-firefox \
  --url https://papergames.io/en/connect4 \
  --mode auto \
  --player auto \
  --weak \
  --poll-ms 250 \
  --post-game-wait-sec 5 \
  --post-game-reload-sec 0
```

Chromium extension mode (optional):

```bash
.venv/bin/python ui/browser_bridge.py \
  --browser chromium \
  --extension-dir /absolute/path/to/unpacked/extension \
  --user-data-dir .pw-user-data \
  --url https://papergames.io/en/connect4 \
  --mode assist
```

Extension notes:
- Extension mode is Chromium-only.
- `--extension-dir` requires headed mode.
- `--user-data-dir` persists extension/profile state.

## Build and Run (CLI)

Build solver:

```bash
make solver
```

Query best move (input ends with `?`):

```bash
printf '16721?\n' | ./solver
```

Query sequence status (input ends with `!`):

```bash
printf '1212121!\n' | ./solver
# -> win1 (possible: ongoing, win1, win2, draw, invalid)
```

Query exact score mode:

```bash
printf '16721\n' | ./solver
```

## Opening Book

Check unresolved entries:

```bash
make book-status
```

Run parallel move backfill (example):

```bash
./tools/parallel_backfill_opening_book_moves.sh \
  --workers 10 \
  --chunk-size 32 \
  --timeout-sec 300 \
  --seq-len 5 \
  --max-minutes 60 \
  --log-every 64 \
  --omit-zero
```

Stop backfill/solver processes:

```bash
pkill -TERM -f parallel_backfill_opening_book_moves.sh || true
pkill -TERM -f '/home/fenari/CPSC481/Project/cpp4/solver$' || true
sleep 2
pkill -KILL -f parallel_backfill_opening_book_moves.sh || true
pkill -KILL -f '/home/fenari/CPSC481/Project/cpp4/solver$' || true
```

## VS Code

- Interpreter is set to `.venv/bin/python` in `.vscode/settings.json`
- Run/Debug configs are in `.vscode/launch.json`:
  - `UI: connect4.py`
  - `UI: connect4vsAI.py`

## Useful Make Targets

```bash
make help
make all
make solver
make tools
make venv
make ui-deps
make ui-connect4
make ui-vsai
make bridge-observe
make bridge-auto
make book-status
make clean
```
