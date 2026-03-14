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

## Build and Run (CLI)

Build solver:

```bash
make solver
```

Query best move (input ends with `?`):

```bash
printf '16721?\n' | ./solver
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
make book-status
make clean
```
