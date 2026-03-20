#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional


BUCKETS = {
    "opening_1_8": (1, 8),
    "mid_9_24": (9, 24),
    "late_25_plus": (25, 42),
}


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * p
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def load_samples(path: Path) -> Dict[str, List[float]]:
    samples = {k: [] for k in BUCKETS}
    if not path.exists():
        return samples

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            think = obj.get("think_time_sec")
            move_index_after = obj.get("move_index_after")
            if not isinstance(think, (int, float)) or not isinstance(move_index_after, int):
                continue

            if BUCKETS["opening_1_8"][0] <= move_index_after <= BUCKETS["opening_1_8"][1]:
                samples["opening_1_8"].append(float(think))
            elif BUCKETS["mid_9_24"][0] <= move_index_after <= BUCKETS["mid_9_24"][1]:
                samples["mid_9_24"].append(float(think))
            elif BUCKETS["late_25_plus"][0] <= move_index_after <= BUCKETS["late_25_plus"][1]:
                samples["late_25_plus"].append(float(think))

    return samples


def recommend_profile(samples: Dict[str, List[float]]) -> Dict[str, float]:
    open_p50 = percentile(samples["opening_1_8"], 0.50) or 1.6
    mid_p50 = percentile(samples["mid_9_24"], 0.50) or 2.9
    late_p50 = percentile(samples["late_25_plus"], 0.50) or mid_p50

    return {
        "len<5": round(clamp(open_p50 * 0.70, 0.6, 1.8), 2),
        "5<=len<9": round(clamp(open_p50 * 1.00, 1.0, 2.4), 2),
        "9<=len<14": round(clamp(mid_p50 * 0.90, 2.0, 3.2), 2),
        "14<=len<22": round(clamp(mid_p50 * 1.00, 2.3, 3.6), 2),
        "22<=len<36": round(clamp((mid_p50 * 0.60) + (late_p50 * 0.40), 2.4, 3.7), 2),
        "len>=36": round(clamp(late_p50 * 0.85, 1.8, 3.2), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recommend next-pass delay profile from opponent move timing samples"
    )
    parser.add_argument(
        "--timings-log",
        default="data/opponent_move_timings.jsonl",
        help="Path to opponent move timing JSONL log",
    )
    parser.add_argument(
        "--min-opening",
        type=int,
        default=250,
        help="Recommended minimum samples for opening bucket",
    )
    parser.add_argument(
        "--min-mid",
        type=int,
        default=400,
        help="Recommended minimum samples for mid bucket",
    )
    parser.add_argument(
        "--min-late",
        type=int,
        default=250,
        help="Recommended minimum samples for late bucket",
    )
    args = parser.parse_args()

    log_path = Path(args.timings_log)
    samples = load_samples(log_path)

    open_vals = samples["opening_1_8"]
    mid_vals = samples["mid_9_24"]
    late_vals = samples["late_25_plus"]

    total = len(open_vals) + len(mid_vals) + len(late_vals)
    print(f"timings_log={log_path}")
    print(f"total_samples={total}")
    print(
        "bucket_samples "
        f"opening={len(open_vals)} mid={len(mid_vals)} late={len(late_vals)}"
    )

    def bucket_stats(name: str, vals: List[float]) -> None:
        p50 = percentile(vals, 0.50)
        p75 = percentile(vals, 0.75)
        p90 = percentile(vals, 0.90)
        print(
            f"{name} "
            f"p50={None if p50 is None else round(p50, 3)} "
            f"p75={None if p75 is None else round(p75, 3)} "
            f"p90={None if p90 is None else round(p90, 3)}"
        )

    bucket_stats("opening_1_8", open_vals)
    bucket_stats("mid_9_24", mid_vals)
    bucket_stats("late_25_plus", late_vals)

    ready = (
        len(open_vals) >= args.min_opening
        and len(mid_vals) >= args.min_mid
        and len(late_vals) >= args.min_late
    )
    print(
        "readiness "
        f"opening={len(open_vals) >= args.min_opening} "
        f"mid={len(mid_vals) >= args.min_mid} "
        f"late={len(late_vals) >= args.min_late} "
        f"overall={ready}"
    )

    rec = recommend_profile(samples)
    print("recommend_delay_profile")
    for key in ["len<5", "5<=len<9", "9<=len<14", "14<=len<22", "22<=len<36", "len>=36"]:
        print(f"  {key}: {rec[key]}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
