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

Auto-mode runtime commands (type into the same terminal and press Enter):
- `pause`: stop after the current game resolves (or pause immediately if not in-game)
- `resume`: resume auto actions
- `status`: print current control state (`running|draining|paused`)
- `wait <sec>`: change terminal post-game wait timer live while bridge is running
- `emote [code]`: send an emote now (`code` defaults to `--win-emote-code`, supports aliases like `scream`, e.g. `emote scream` or `emote 1f631`)
- `emote help`: show emote aliases and corresponding hex codes
- `quit`: exit bridge cleanly

Operator prompt UX:
- When `prompt_toolkit` is installed, bridge shows a pinned `[bridge cmd]` prompt line so commands are easier to enter while logs scroll.
- In pinned prompt mode, `Ctrl+C` triggers a graceful `quit`.

Key options:
- `--player 1|2|auto`: choose your side (or prompt at runtime with `auto`)
- `--weak`: use weak solver mode (`./solver -w`)
- `--auto-rematch`: in auto mode, click Rematch after terminal state when available (default behavior is leave room)
- `--auto-emote-on-win`: in auto papergames mode, send an emote after wins
- `--win-emote-code VALUE`: emoji value to click on win (hex or alias; default `1f60e` / `sunglasses`). Aliases: `scream`, `sunglasses`, `smirk`, `cry`, `sob`, `wave`, `thumbsup`, `wink`, `tongue`, `sleep`, `zipper`, `grin`
- `--post-game-wait-sec N`: in auto papergames mode, wait `N` seconds on terminal page before leave/rematch actions (default `5`)
- `--auto-max-runtime-sec N`: in auto mode, after `N` seconds request drain, then quit automatically once current game resolves (`0` disables, default). Hard safety cutoff is `2N` seconds and force-quits even mid-match.
- `--post-game-reload-sec N`: in auto papergames mode, reload lobby if post-game controls do not appear within `N` seconds (`0` disables, default)
- `--stats-json PATH`: persistent bridge stats summary JSON (default `data/bridge_stats.json`)
- `--stats-csv PATH`: per-game history CSV (default `data/bridge_match_history.csv`)
- `--stats-reset`: clear existing bridge stats files at startup
- `--our-username NAME`: exclude your own username from strict opponent capture (rank suffixes like `"name 7850"` are normalized)
- `--manual-fallback --manual-input-mode incremental|full`: fallback input when board parsing fails
- `--config ui/browser_targets.papergames.json`: custom board selector config

Current papergames behavior:
- Treats a match as active only when URL is a live room route (`/en/r/<roomcode>`), avoiding premature attach on queue routes (`/en/q/<...>`).
- Uses papergames-specific parsing with grid column-count deltas (`source=grid-delta`) to track moves robustly.
- Uses solver status endpoint (`sequence!`) to validate snapshots and detect `win1|win2|draw|invalid`.
- In auto mode, after terminal state it waits `--post-game-wait-sec` (default `5s`) before leave/rematch actions.
- In auto mode, `--auto-max-runtime-sec` enforces a dual timeout: soft at `N` (drain, quit after game resolves) and hard at `2N` (force quit if game end is not detected).
- Timeout-triggered exits print a one-line session summary for that run window.
- If post-game controls are delayed or missing, auto mode can optionally fall back to reloading `https://papergames.io/en/connect4` after the configured timeout and resumes matchmaking.
- If opponent leaves/disconnects, auto mode leaves room and starts new matchmaking.
- After leaving room in auto mode, it clicks `Play online` with random player and continues.
- If the lobby remains idle after a queue click, auto mode retries queue controls automatically.
- If a game abort/disconnect causes sequence loss, auto mode recovers via terminal/post-game/lobby detection and resumes lifecycle flow.
- Suppresses duplicate suggestion spam and prints opponent move lines when detected.
- Records slow exact solves (`>10s`) to `data/slow_solve_prefixes.log` (one entry per sequence per run) for targeted opening-book expansion.
- Tracks persistent results in `data/bridge_stats.json`: overall W/L/D, side-specific (`P1`/`P2`) W/L/D, and per-opponent stats (games, W/L/D, last seen, side counts, solve-time aggregates).
- Appends per-game rows to `data/bridge_match_history.csv`.

View bridge stats files:

```bash
# Pretty-print persistent summary stats
python3 -m json.tool data/bridge_stats.json

# View recent per-game results (header + last 20 rows)
{ head -n 1 data/bridge_match_history.csv; tail -n 20 data/bridge_match_history.csv; } | column -s, -t
```

Second-pass delay tuning helper (after collecting more games):

```bash
.venv/bin/python tools/recommend_delay_profile.py
```

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

## Linux App Icon / Launcher

This repo includes a desktop launcher and icon for Bridge:

- `ui/Connect4-Bridge.desktop`
- `ui/launch_bridge.sh`
- `ui/assets/bridge-icon.svg`

Install the launcher into your app menu/dock:

```bash
mkdir -p ~/.local/share/applications
sed "s|__CONNECT4_BOT_ROOT__|$(pwd)|g" ui/Connect4-Bridge.desktop > ~/.local/share/applications/Connect4-Bridge.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

After this, search for `Connect4 Bridge` in your desktop launcher.

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
pkill -TERM -f '(^|/)solver$' || true
sleep 2
pkill -KILL -f parallel_backfill_opening_book_moves.sh || true
pkill -KILL -f '(^|/)solver$' || true
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
