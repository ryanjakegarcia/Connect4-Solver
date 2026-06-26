#!/usr/bin/env python3
"""Generate early-game (ply 0-N) labeled dataset via solver self-play.

Plays solver-vs-solver from randomized openings, recording (sequence, best_move,
move_count) for every position up to --max-ply. Output CSV matches the schema
of UCI-Midgame-d30.csv and is compatible with train_phase_policy.py.

Usage (from repo root):
    python scripts/generate_early_game_dataset.py \
        --solver build/solver \
        --out data/early_game.csv \
        --rows 3000 \
        --seed 42
"""

from __future__ import annotations

import argparse
import csv
import random
import subprocess
import sys
import time
from pathlib import Path

ROWS = 6
COLS = 7


# ---------------------------------------------------------------------------
# Board (stdlib-only, no numpy)
# ---------------------------------------------------------------------------

class Board:
    def __init__(self) -> None:
        self.grid = [[0] * COLS for _ in range(ROWS)]
        self.heights = [0] * COLS
        self.move_count = 0
        self.terminal = False

    def can_play(self, col: int) -> bool:
        return 0 <= col < COLS and self.heights[col] < ROWS

    def legal_moves(self) -> list[int]:
        return [c + 1 for c in range(COLS) if self.can_play(c)]

    def play(self, col_1based: int) -> None:
        col = col_1based - 1
        row = self.heights[col]
        player = 1 if self.move_count % 2 == 0 else 2
        self.grid[row][col] = player
        self.heights[col] += 1
        self.move_count += 1
        if self._is_win(row, col, player) or self.move_count == ROWS * COLS:
            self.terminal = True

    def _is_win(self, row: int, col: int, player: int) -> bool:
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            count = 1
            for sign in (-1, 1):
                cx, cy = col + sign * dx, row + sign * dy
                while 0 <= cx < COLS and 0 <= cy < ROWS and self.grid[cy][cx] == player:
                    count += 1
                    cx += sign * dx
                    cy += sign * dy
            if count >= 4:
                return True
        return False


# ---------------------------------------------------------------------------
# Solver subprocess (persistent)
# ---------------------------------------------------------------------------

class Solver:
    def __init__(self, binary: str, weak: bool = True) -> None:
        cmd = [binary]
        if weak:
            cmd.append("-w")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def best_move(self, sequence: str) -> int:
        """Return 1-based best column for given sequence."""
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(f"{sequence}?\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline().strip()
        col = int(line)
        if not 1 <= col <= 7:
            raise ValueError(f"Solver returned invalid column {col} for seq {sequence!r}")
        return col

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()


# ---------------------------------------------------------------------------
# Mirror dedup
# ---------------------------------------------------------------------------

def mirror_seq(seq: str) -> str:
    return "".join(str(8 - int(c)) for c in seq)

def canonical(seq: str) -> str:
    m = mirror_seq(seq)
    return seq if seq <= m else m


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def generate(
    solver: Solver,
    rng: random.Random,
    max_ply: int,
    seed_moves: int,
    seen: set[str],
    writer: "csv.DictWriter",
    out_file,
    total_written: list[int],
    target_rows: int,
    verbose: bool,
) -> None:
    """Run one self-play game, writing new positions immediately to CSV."""
    board = Board()
    sequence = ""

    # Random seed moves for opening variety.
    for _ in range(seed_moves):
        legal = board.legal_moves()
        if not legal or board.terminal:
            break
        col = rng.choice(legal)
        board.play(col)
        sequence += str(col)
        if board.terminal or len(sequence) > max_ply:
            return

    # Solver plays both sides, recording every position up to max_ply.
    while not board.terminal and len(sequence) <= max_ply:
        key = canonical(sequence)
        if key not in seen:
            try:
                best = solver.best_move(sequence)
            except (ValueError, OSError) as exc:
                if verbose:
                    print(f"[warn] solver error at seq={sequence!r}: {exc}")
                break
            seen.add(key)
            writer.writerow({"sequence": sequence, "best_move": best, "move_count": len(sequence)})
            out_file.flush()
            total_written[0] += 1
            if total_written[0] % 100 == 0 and verbose:
                print(f"  {total_written[0]}/{target_rows} rows written ({len(seen)} unique canonical)")
            board.play(best)
            sequence += str(best)
        else:
            # Position already seen — advance with solver move but don't record.
            try:
                best = solver.best_move(sequence)
            except (ValueError, OSError):
                break
            board.play(best)
            sequence += str(best)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--solver", default="build/solver", help="Path to C++ solver binary")
    parser.add_argument("--weak", action=argparse.BooleanOptionalAction, default=True, help="Use weak solver mode (-w), faster for early positions (default: on)")
    parser.add_argument("--out", required=True, help="Output CSV path")
    parser.add_argument("--rows", type=int, default=3000, help="Target unique rows to collect")
    parser.add_argument("--max-ply", type=int, default=12, help="Max move count to record (inclusive)")
    parser.add_argument("--seed-moves", type=int, default=2, help="Random moves before solver takes over (min 1, for opening variety)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--max-games", type=int, default=50000, help="Safety cap on game iterations")
    parser.add_argument("--append", action="store_true", help="Append to existing CSV instead of overwriting")
    parser.add_argument("--verbose", action="store_true", help="Print progress")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    solver_path = Path(args.solver)
    out_path = Path(args.out)

    if not solver_path.exists():
        print(f"Solver not found: {solver_path}")
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing seen keys if appending.
    seen: set[str] = set()
    existing_rows = 0
    if args.append and out_path.exists():
        with out_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                seen.add(canonical(row["sequence"]))
                existing_rows += 1
        print(f"Resuming: {existing_rows} existing rows, {len(seen)} canonical positions")

    rng = random.Random(args.seed)
    solver = Solver(str(solver_path), weak=args.weak)

    print(f"Solver:    {solver_path}")
    print(f"Output:    {out_path}")
    print(f"Target:    {args.rows} new unique rows  (max_ply={args.max_ply}, seed_moves={args.seed_moves})")
    print(f"Seed:      {args.seed}")
    print()

    mode = "a" if (args.append and out_path.exists() and existing_rows > 0) else "w"
    total_written = [0]  # mutable counter passed into generate()

    t0 = time.time()
    games = 0
    try:
        with out_path.open(mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["sequence", "best_move", "move_count"])
            if mode == "w":
                writer.writeheader()
                f.flush()
            while total_written[0] < args.rows and games < args.max_games:
                s = rng.randint(1, max(1, args.seed_moves))  # min 1 seed move — skip ply-0 solve
                before = total_written[0]
                generate(solver, rng, args.max_ply, s, seen, writer, f, total_written, args.rows, args.verbose)
                games += 1
                new_this_game = total_written[0] - before
                elapsed = time.time() - t0
                print(f"Game {games:>5}  +{new_this_game} new  total={total_written[0]}/{args.rows}  elapsed={elapsed:.1f}s")
    finally:
        solver.close()

    elapsed = time.time() - t0
    total = existing_rows + total_written[0]
    print()
    print(f"Done. Games played: {games}  New rows: {total_written[0]}  Total in file: {total}")
    print(f"Elapsed: {elapsed:.1f}s  ({total_written[0]/max(elapsed,0.001):.0f} rows/sec)")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
