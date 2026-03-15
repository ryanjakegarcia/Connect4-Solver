import csv
import json
import os
import time
from typing import Optional


class BridgeStats:
    def __init__(self, json_path: str, csv_path: str) -> None:
        self.json_path = json_path
        self.csv_path = csv_path
        self.data = self._default_data()

    @staticmethod
    def _default_record() -> dict:
        return {"games": 0, "wins": 0, "losses": 0, "draws": 0}

    @classmethod
    def _default_data(cls) -> dict:
        return {
            "version": 1,
            "updated_at": None,
            "totals": cls._default_record(),
            "by_side": {"1": cls._default_record(), "2": cls._default_record()},
            "opponents": {},
        }

    def load(self) -> None:
        if not os.path.exists(self.json_path):
            self.data = self._default_data()
            return
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.data = raw
            else:
                self.data = self._default_data()
        except Exception:
            self.data = self._default_data()

    def reset(self) -> None:
        self.data = self._default_data()
        for p in (self.json_path, self.csv_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    def _ensure_record(self, container: dict, key: str) -> dict:
        rec = container.get(key)
        if not isinstance(rec, dict):
            rec = self._default_record()
            container[key] = rec
        for k in ("games", "wins", "losses", "draws"):
            if not isinstance(rec.get(k), int):
                rec[k] = 0
        return rec

    def _bump(self, rec: dict, result: str) -> None:
        rec["games"] += 1
        if result == "win":
            rec["wins"] += 1
        elif result == "loss":
            rec["losses"] += 1
        elif result == "draw":
            rec["draws"] += 1

    def record_game(
        self,
        result: str,
        our_side: Optional[int],
        opponent: Optional[str],
        sequence_len: int,
        solve_samples: int,
        solve_total_sec: float,
    ) -> None:
        if result not in {"win", "loss", "draw"}:
            return

        totals = self._ensure_record(self.data, "totals")
        self._bump(totals, result)

        side_str = str(our_side) if our_side in {1, 2} else None
        if side_str is not None:
            by_side = self.data.setdefault("by_side", {"1": self._default_record(), "2": self._default_record()})
            side_rec = self._ensure_record(by_side, side_str)
            self._bump(side_rec, result)

        if opponent:
            opps = self.data.setdefault("opponents", {})
            entry = opps.get(opponent)
            if not isinstance(entry, dict):
                entry = {
                    "games": 0,
                    "wins": 0,
                    "losses": 0,
                    "draws": 0,
                    "last_seen": None,
                    "our_side_counts": {"1": 0, "2": 0},
                    "solve_samples": 0,
                    "solve_total_sec": 0.0,
                    "avg_solve_sec": 0.0,
                }
                opps[opponent] = entry

            self._bump(entry, result)
            entry["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            if side_str is not None:
                side_counts = entry.setdefault("our_side_counts", {"1": 0, "2": 0})
                side_counts[side_str] = int(side_counts.get(side_str, 0)) + 1

            entry["solve_samples"] = int(entry.get("solve_samples", 0)) + solve_samples
            entry["solve_total_sec"] = float(entry.get("solve_total_sec", 0.0)) + solve_total_sec
            samples = max(1, int(entry["solve_samples"]))
            entry["avg_solve_sec"] = float(entry["solve_total_sec"]) / float(samples)

        self.data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self._append_csv(result, our_side, opponent, sequence_len, solve_samples, solve_total_sec)
        self.save()

    def _append_csv(
        self,
        result: str,
        our_side: Optional[int],
        opponent: Optional[str],
        sequence_len: int,
        solve_samples: int,
        solve_total_sec: float,
    ) -> None:
        try:
            os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
            exists = os.path.exists(self.csv_path)
            with open(self.csv_path, "a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if not exists:
                    w.writerow([
                        "timestamp",
                        "result",
                        "our_side",
                        "opponent",
                        "sequence_len",
                        "solve_samples",
                        "solve_total_sec",
                    ])
                w.writerow(
                    [
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                        result,
                        "" if our_side is None else our_side,
                        "" if opponent is None else opponent,
                        sequence_len,
                        solve_samples,
                        f"{solve_total_sec:.3f}",
                    ]
                )
        except Exception:
            pass

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
        except Exception:
            pass

    def summary_line(self) -> str:
        totals = self._ensure_record(self.data, "totals")
        by_side = self.data.setdefault("by_side", {"1": self._default_record(), "2": self._default_record()})
        p1 = self._ensure_record(by_side, "1")
        p2 = self._ensure_record(by_side, "2")
        return (
            f"overall W-L-D={totals['wins']}-{totals['losses']}-{totals['draws']} "
            f"(games={totals['games']}) | "
            f"P1 W-L-D={p1['wins']}-{p1['losses']}-{p1['draws']} | "
            f"P2 W-L-D={p2['wins']}-{p2['losses']}-{p2['draws']}"
        )


def result_from_seq_status(seq_status: str, our_side: Optional[int]) -> Optional[str]:
    if seq_status == "draw":
        return "draw"
    if our_side not in {1, 2}:
        return None
    if seq_status == "win1":
        return "win" if our_side == 1 else "loss"
    if seq_status == "win2":
        return "win" if our_side == 2 else "loss"
    return None


def result_from_terminal_reason(reason: str, our_side: Optional[int]) -> Optional[str]:
    if our_side not in {1, 2}:
        return None
    r = reason.lower()
    if "draw" in r:
        return "draw"
    if "you won" in r:
        return "win"
    if "you lost" in r:
        return "loss"
    if "timed out" in r or "timeout" in r:
        return "loss"
    if "opponent left" in r or "opponent disconnected" in r:
        return "win"
    return None
