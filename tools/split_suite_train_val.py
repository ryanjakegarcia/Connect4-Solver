#!/usr/bin/env python3
import argparse
import random
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministically split a suite file into train/val text files"
    )
    parser.add_argument("--input", required=True, help="Input suite file (one sequence per line)")
    parser.add_argument("--train-out", required=True, help="Output train suite file")
    parser.add_argument("--val-out", required=True, help="Output validation suite file")
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.1,
        help="Validation ratio in [0,1] (default: 0.1)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=481,
        help="Shuffle seed for deterministic split (default: 481)",
    )
    parser.add_argument(
        "--shuffle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Shuffle before splitting (default: true)",
    )
    return parser.parse_args()


def read_lines(path: Path) -> list[str]:
    lines: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)
    return lines


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in lines:
            f.write(row)
            f.write("\n")


def main() -> int:
    args = parse_args()

    if not (0.0 < args.val_ratio < 1.0):
        raise SystemExit("--val-ratio must be between 0 and 1")

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    rows = read_lines(input_path)
    if len(rows) < 2:
        raise SystemExit("Need at least 2 rows to split train/val")

    indices = list(range(len(rows)))
    if args.shuffle:
        rng = random.Random(args.seed)
        rng.shuffle(indices)

    val_count = max(1, int(round(len(rows) * args.val_ratio)))
    val_idx = set(indices[:val_count])

    train_rows: list[str] = []
    val_rows: list[str] = []
    for i, row in enumerate(rows):
        if i in val_idx:
            val_rows.append(row)
        else:
            train_rows.append(row)

    train_out = Path(args.train_out)
    val_out = Path(args.val_out)
    write_lines(train_out, train_rows)
    write_lines(val_out, val_rows)

    print(f"Input rows: {len(rows)}")
    print(f"Train rows: {len(train_rows)}")
    print(f"Val rows:   {len(val_rows)}")
    print(f"Train file: {train_out}")
    print(f"Val file:   {val_out}")
    print(f"Shuffle: {args.shuffle} (seed={args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
