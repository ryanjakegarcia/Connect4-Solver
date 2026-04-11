#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

VALID_SEQ = set("1234567")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run generate_self_play_suites.py in parallel shards and merge deterministically"
    )
    parser.add_argument("--input-log", required=True, help="Input JSONL log")
    parser.add_argument("--solver", required=True, help="Solver binary path")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to run shard jobs",
    )
    parser.add_argument(
        "--generator",
        default="tools/generate_self_play_suites.py",
        help="Path to suite generator script",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="Final merged suite output file",
    )
    parser.add_argument("--move-count-min", type=int, default=12)
    parser.add_argument("--move-count-max", type=int, default=28)
    parser.add_argument("--difficulty-threshold", type=int, default=2)
    parser.add_argument("--max-per-file", type=int, default=2000)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument("--canonical", action="store_true")
    parser.add_argument("--shards", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    parser.add_argument("--jobs", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    parser.add_argument("--work-dir", default="data/suite_shards")
    parser.add_argument("--keep-shards", action="store_true")
    return parser.parse_args()


def mirror_sequence(seq: str) -> str:
    return "".join(str(8 - int(ch)) for ch in seq)


def canonical_sequence(seq: str) -> str:
    mirrored = mirror_sequence(seq)
    return seq if seq <= mirrored else mirrored


def is_valid_seq(seq: str) -> bool:
    return bool(seq) and len(seq) <= 42 and all(ch in VALID_SEQ for ch in seq)


def split_input_round_robin(input_log: Path, shard_inputs: list[Path]) -> tuple[int, int]:
    for p in shard_inputs:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")

    writers = [p.open("a", encoding="utf-8") for p in shard_inputs]
    total = 0
    invalid = 0
    idx = 0
    try:
        with input_log.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                seq = row.get("sequence")
                if not isinstance(seq, str) or not is_valid_seq(seq):
                    invalid += 1
                    continue
                payload = json.dumps({"sequence": seq}, sort_keys=True)
                writers[idx].write(payload + "\n")
                idx = (idx + 1) % len(writers)
                total += 1
    finally:
        for w in writers:
            w.close()

    return total, invalid


def run_one_shard(
    *,
    shard_id: int,
    args: argparse.Namespace,
    shard_input: Path,
    shard_output: Path,
    shard_log: Path,
) -> tuple[int, float, int]:
    cmd = [
        args.python,
        args.generator,
        "--input-log",
        str(shard_input),
        "--solver",
        args.solver,
        "--output-file",
        str(shard_output),
        "--move-count-min",
        str(args.move_count_min),
        "--move-count-max",
        str(args.move_count_max),
        "--difficulty-threshold",
        str(args.difficulty_threshold),
        "--max-per-file",
        str(10**9),
        "--batch-size",
        str(args.batch_size),
        "--timeout-sec",
        str(args.timeout_sec),
    ]
    if args.canonical:
        cmd.append("--canonical")

    start = time.time()
    with shard_log.open("w", encoding="utf-8") as logf:
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, check=False)
    elapsed = time.time() - start

    if proc.returncode != 0:
        raise RuntimeError(f"shard {shard_id} failed (code={proc.returncode}); see {shard_log}")

    out_count = 0
    if shard_output.exists():
        with shard_output.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out_count += 1

    return shard_id, elapsed, out_count


def merge_outputs(
    shard_outputs: list[Path],
    output_file: Path,
    max_per_file: int,
    canonical: bool,
) -> tuple[int, int, bool]:
    seen: set[str] = set()
    merged: list[str] = []

    for p in shard_outputs:
        if not p.exists():
            continue
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                seq = line.strip()
                if not seq or not is_valid_seq(seq):
                    continue
                key = canonical_sequence(seq) if canonical else seq
                if key in seen:
                    continue
                seen.add(key)
                merged.append(seq)

    merged.sort(key=lambda s: (len(s), s))
    truncated = len(merged) > max_per_file
    written = merged[:max_per_file]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        for seq in written:
            f.write(seq + "\n")

    return len(merged), len(written), truncated


def main() -> int:
    args = parse_args()

    if args.shards < 1 or args.jobs < 1:
        print("--shards and --jobs must be >= 1", file=sys.stderr)
        return 2

    input_log = Path(args.input_log)
    if not input_log.exists():
        print(f"Input log not found: {input_log}", file=sys.stderr)
        return 2

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    shard_inputs = [work_dir / f"in_{i:03d}.jsonl" for i in range(args.shards)]
    shard_outputs = [work_dir / f"out_{i:03d}.txt" for i in range(args.shards)]
    shard_logs = [work_dir / f"run_{i:03d}.log" for i in range(args.shards)]

    total_valid, invalid_rows = split_input_round_robin(input_log, shard_inputs)
    print(
        f"Shard split complete: valid_sequences={total_valid} invalid_rows={invalid_rows} shards={args.shards}"
    )

    start = time.time()
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = {
            ex.submit(
                run_one_shard,
                shard_id=i,
                args=args,
                shard_input=shard_inputs[i],
                shard_output=shard_outputs[i],
                shard_log=shard_logs[i],
            ): i
            for i in range(args.shards)
        }
        for fut in as_completed(futures):
            shard_id = futures[fut]
            try:
                sid, elapsed, out_count = fut.result()
            except Exception as exc:
                print(f"Shard {shard_id} failed: {exc}", file=sys.stderr)
                return 1
            print(f"Shard {sid} done: elapsed={elapsed:.1f}s outputs={out_count} log={shard_logs[sid]}")

    merged_total, written_count, truncated = merge_outputs(
        shard_outputs=shard_outputs,
        output_file=Path(args.output_file),
        max_per_file=args.max_per_file,
        canonical=args.canonical,
    )

    elapsed = time.time() - start
    print(f"Merged unique sequences: {merged_total}")
    if truncated:
        print(f"Output truncated to --max-per-file: {written_count}")
    else:
        print(f"Output written: {written_count}")
    print(f"Final output: {args.output_file}")
    print(f"Elapsed: {elapsed:.1f}s")

    if not args.keep_shards:
        for p in shard_inputs + shard_outputs:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
