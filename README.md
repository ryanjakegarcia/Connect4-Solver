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

Windows build targets are planned but not the primary flow yet.

## Quick Start

Build and install everything:

```bash
make setup
```

Compiled binaries are produced under `build/` and linked at repo root for compatibility (`./solver`, `./backfill_opening_book_scores`, `./backfill_opening_book_moves`).

Run on website (primary):

```bash
make bridge MODE=auto
```

Optional (recommended): provide your username so self-opponent filtering is more reliable:

```bash
make bridge MODE=auto BRIDGE_USERNAME="Your Username"
```

Alternate mode (watch/suggest only, no auto-clicks):

```bash
make bridge MODE=observe
```

> **Note:** observe mode has known instability. Prefer `assist` or `standby` for reliable sessions.

Confirmation mode (prompts before each click):

```bash
make bridge MODE=assist
```

Standby mode (auto-play in active games, operator starts each match manually):

```bash
make bridge MODE=standby
```

Manual setup equivalent:

```bash
make venv
make ui-deps
make all
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

Recommended auto mode:

```bash
make bridge MODE=auto
```

With username:

```bash
make bridge MODE=auto BRIDGE_USERNAME="Your Username"
```

If you want non-clicking observation mode:

```bash
make bridge MODE=observe
```

With username:

```bash
make bridge MODE=observe BRIDGE_USERNAME="Your Username"
```

If you want confirmation-before-click mode:

```bash
make bridge MODE=assist
```

With username:

```bash
make bridge MODE=assist BRIDGE_USERNAME="Your Username"
```

If you want auto-play only after you manually enter a game:

```bash
make bridge MODE=standby
```

With username:

```bash
make bridge MODE=standby BRIDGE_USERNAME="Your Username"
```

Bridge modes:

- `observe`: parse board and print suggestions only
- `assist`: prompt before click actions
- `auto`: drive actions automatically, including queue/start flow between games
- `standby`: auto-play only during an active game; operator starts games manually

Common runtime commands in auto/standby mode (enter in the same terminal):

- `pause`: drain and pause after current game (or pause immediately if idle)
- `resume`: resume automation
- `start`: request matchmaking now (auto mode only)
- `status`: print runtime state
- `wait <sec>`: change post-game wait timer live
- `delay [x]`: view/set delay scale (`0.00` to `1.80`)
- `emote [code]`: send an emote (hex code or alias)
- `board`: print current parsed board state
- `info`: print session info and current settings
- `clear`: clear terminal output
- `quit`: clean shutdown

Useful auto flags:

- `--player 1|2|auto`
- `--weak`
- `--auto-rematch` — click Rematch after each game instead of leaving the room
- `--post-game-wait-sec N`
- `--post-game-reload-sec N`
- `--auto-max-runtime-sec N`
- `--auto-max-games N` — stop after N completed games in the session
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

```bash
make help
make setup
make bridge MODE=auto
make bridge MODE=auto BRIDGE_USERNAME="Your Username"
make bridge MODE=standby
make bridge MODE=observe
make bridge MODE=assist
make local-ui
make local-vsai
make solver
make ci-smoke
make book-status
make clean
```

## FAQ

### How do I start quickly?

Run:

```bash
make setup
make bridge MODE=auto
```

If you want strict self-username handling in bridge stats/opponent capture:

```bash
make bridge MODE=auto BRIDGE_USERNAME="Your Username"
```

### Should I use `observe`, `assist`, `standby`, or `auto` mode?

- `observe`: reads the board and prints suggestions only
- `assist`: asks for confirmation before each click
- `standby`: auto-plays once you are in a live game, but does not start the next game for you
- `auto`: reads the board and performs clicks automatically, including queue/start flow

If you are testing selectors, layout, or timing, start with `observe`, then `assist`, then `standby`, then `auto`.

### The solver is missing or not found. What do I do?

Run:

```bash
make solver
```

Then verify:

```bash
ls -l solver build/solver
```

### The website UI changed and automation is failing. What now?

- Retry in `observe` mode first to confirm parsing still works.
- Re-run setup deps to ensure browsers/deps are current: `make ui-deps`.
- If controls/selectors changed upstream, bridge selector/runtime updates may be required in the repo.

### How do I pause/resume/quit while running auto mode?

Type runtime commands in the same bridge terminal:

- `pause`
- `resume`
- `quit`
- `status`

### Is observe mode fully stable?

Not yet. Observe mode has known bugs and may occasionally behave inconsistently. If you hit issues, prefer `assist` or `auto` mode for now and report the observed behavior/sequence.

## References

- http://blog.gamesolver.org/
- https://www.youtube.com/watch?v=MMLtza3CZFM
- https://en.wikipedia.org/wiki/Minimax
- https://en.wikipedia.org/wiki/Negamax
