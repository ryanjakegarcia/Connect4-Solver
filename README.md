# Connect4 Bot

Create a competitive Connect-4 bot powered by a C++ solver and practical automation tooling.

## What Is In This Repo

- `src/`: C++17 solver and core engine headers
- `ui/`: local game UIs and browser bridge runtime
- `data/`: opening-book and bridge stats/history files
- `tools/`: utility scripts and tuning helpers
- `suites/`: benchmark/test position suites

## Prerequisites

- Linux/macOS shell
- `g++` with C++17 support
- Python 3.10+

## Quick Start

Build binaries and Python environment:

```bash
make setup
```

Compiled binaries are produced under `build/` and linked at repo root for compatibility (`./solver`, `./backfill_opening_book_scores`, `./backfill_opening_book_moves`).

Manual equivalent:

```bash
make venv
make ui-deps
make all
```

## Command Cheatsheet

```bash
# first-time setup
make setup

# rebuild C++ binaries only
make all

# run local UIs
make local-ui
make local-vsai

# run browser bridge
make bridge MODE=observe
make bridge MODE=auto

# diagnostics
make check-build-env
make check-ui-env
make check-bridge-env

# CI workflows
make help-ci
make ci-setup
make ci-build
make ci-fast
make ci-check
make ci-full
make ci-smoke
```

Run local UIs:

```bash
make local-ui
# or
make local-vsai
```

## CLI Solver Usage

Build solver only:

```bash
make solver
```

This creates `build/solver` and refreshes the root compatibility link `./solver`.

Best move query (`?`):

```bash
printf '16721?\n' | ./solver
```

Position status query (`!`):

```bash
printf '1212121!\n' | ./solver
# -> ongoing | win1 | win2 | draw | invalid
```

Exact score query (no suffix):

```bash
printf '16721\n' | ./solver
```

## Browser Bridge

The bridge connects solver decisions to a live browser game.

Install once:

```bash
make ui-deps
```

Recommended observe mode:

```bash
make bridge MODE=observe
```

Recommended auto mode:

```bash
make bridge MODE=auto
```

Bridge modes:

- `observe`: parse board and print suggestions only
- `assist`: prompt before click actions
- `auto`: drive actions automatically

Common runtime commands in auto mode (enter in the same terminal):

- `pause`: drain and pause after current game (or pause immediately if idle)
- `resume`: resume automation
- `start`: request matchmaking now
- `status`: print runtime state
- `wait <sec>`: change post-game wait timer live
- `delay [x]`: view/set delay scale (`0.00` to `1.80`)
- `quit`: clean shutdown

Useful auto flags:

- `--player 1|2|auto`
- `--weak`
- `--post-game-wait-sec N`
- `--post-game-reload-sec N`
- `--auto-max-runtime-sec N`
- `--stats-json PATH`
- `--stats-csv PATH`
- `--stats-reset`

Caution: browser automation is sensitive to site UI/DOM changes and may require selector/runtime updates.

View bridge stats quickly:

```bash
python3 -m json.tool data/bridge_stats.json
{ head -n 1 data/bridge_match_history.csv; tail -n 20 data/bridge_match_history.csv; } | column -s, -t
```

Delay tuning helper (after collecting games):

```bash
.venv/bin/python tools/recommend_delay_profile.py
```

## Linux Launcher (Optional)

Repo files:

- `ui/Connect4-Bridge.desktop`
- `ui/launch_bridge.sh`
- `ui/assets/bridge-icon.svg`

Install to desktop menu:

```bash
mkdir -p ~/.local/share/applications
sed "s|__CONNECT4_BOT_ROOT__|$(pwd)|g" ui/Connect4-Bridge.desktop > ~/.local/share/applications/Connect4-Bridge.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

## Useful Make Targets

Use `make help` to view the current grouped target list and descriptions.

## References

- http://blog.gamesolver.org/
- https://www.youtube.com/watch?v=MMLtza3CZFM&t=5117s
- https://en.wikipedia.org/wiki/Minimax
- https://en.wikipedia.org/wiki/Negamax
