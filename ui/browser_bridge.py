#!/usr/bin/env python3
import argparse
from contextlib import nullcontext
import json
import os
import queue
import re
import select
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception:
    PromptSession = None

    def patch_stdout(*args, **kwargs):
        return nullcontext()

from bridge.actions import play_column, click_leave_room, click_rematch, try_click_queue_controls
from bridge.opponent import read_opponent_username_strict, sanitize_username, usernames_equivalent
from bridge.parsing import (
    is_our_turn,
    in_lobby_url,
    read_grid_column_counts,
    infer_single_move_from_count_delta,
    has_same_column_counts,
    read_sequence,
    probe_sequence,
    is_replay_page,
    has_in_game_ui,
    has_initial_your_turn_text,
    detect_terminal_page_reason,
    read_post_game_ui_state,
)
from bridge.state import RuntimeResetState
from bridge.stats import BridgeStats, result_from_seq_status, result_from_terminal_reason

VALID_SEQ_RE = re.compile(r"^[1-7]*$")
BRIDGE_JS = r"""
(() => {
    const state = {
        selectors: [],
        siteMode: 'generic',
        lastGridColumnCounts: null,
    };

    const MOVE_KEY_HINTS = [
        "move", "moves", "history", "sequence", "turn",
        "board", "connect", "connect4", "c4", "game", "match", "room",
    ];

    function hasMoveHint(text) {
        if (!text) return false;
        const lower = String(text).toLowerCase();
        return MOVE_KEY_HINTS.some((h) => lower.includes(h));
    }

    function isPlausibleSequence(seq) {
        if (typeof seq !== "string") return false;
        if (!/^[1-7]{1,42}$/.test(seq)) return false;
        const heights = Array(7).fill(0);
        for (const ch of seq) {
            const col = Number(ch) - 1;
            heights[col] += 1;
            if (heights[col] > 6) return false;
        }
        return true;
    }

    function scoreElement(el) {
        if (!el || !el.getBoundingClientRect) return -1;
        const r = el.getBoundingClientRect();
        if (r.width < 200 || r.height < 150) return -1;

        let score = r.width * r.height;
        const ratio = r.width / r.height;
        score -= Math.abs(ratio - (7 / 6)) * 10000;

        const tag = (el.tagName || "").toLowerCase();
        if (tag === "canvas") score += 2000;

        const attrs = ((el.id || "") + " " + (el.className || "")).toLowerCase();
        if (attrs.includes("connect")) score += 1500;
        if (attrs.includes("board")) score += 1500;
        if (attrs.includes("game")) score += 500;

        return score;
    }

    function detectBoardElement() {
        const seen = new Set();
        const candidates = [];

        for (const sel of state.selectors) {
            const els = document.querySelectorAll(sel);
            for (const el of els) {
                if (seen.has(el)) continue;
                seen.add(el);
                const score = scoreElement(el);
                if (score >= 0) candidates.push({ el, score });
            }
        }

        for (const el of document.querySelectorAll("canvas, div")) {
            if (seen.has(el)) continue;
            const score = scoreElement(el);
            if (score >= 0) candidates.push({ el, score });
        }

        if (!candidates.length) return null;
        candidates.sort((a, b) => b.score - a.score);
        return candidates[0].el;
    }

    function boardRect() {
        const board = detectBoardElement();
        if (!board) return null;
        const r = board.getBoundingClientRect();
        return {
            x: r.left,
            y: r.top,
            width: r.width,
            height: r.height,
            selectorHint: board.tagName,
        };
    }

    function parseSequenceFromCells() {
        const cells = Array.from(document.querySelectorAll("[data-col][data-row], [data-column][data-row], [data-x][data-y]"));
        if (!cells.length) return null;

        const rows = 6;
        const cols = 7;
        const grid = Array.from({ length: rows }, () => Array(cols).fill(0));

        function getNum(el, names) {
            for (const n of names) {
                const v = el.getAttribute(n);
                if (v != null && /^-?\d+$/.test(v)) return parseInt(v, 10);
            }
            return null;
        }

        for (const el of cells) {
            const col = getNum(el, ["data-col", "data-column", "data-x"]);
            const row = getNum(el, ["data-row", "data-y"]);
            if (col == null || row == null) continue;
            if (col < 0 || col >= cols || row < 0 || row >= rows) continue;

            const cls = ((el.className || "") + " " + (el.getAttribute("data-player") || "")).toLowerCase();
            let piece = 0;
            if (cls.includes("red") || cls.includes("p1") || cls.includes("player1") || cls.includes("yellow") || cls.includes("p2") || cls.includes("player2") || cls.includes("filled")) {
                piece = 1;
            }
            if (piece) grid[row][col] = 1;
        }

        const seq = [];
        const heights = Array(cols).fill(0);
        let placed = 0;
        while (placed < rows * cols) {
            let progressed = false;
            for (let c = 0; c < cols; c++) {
                const h = heights[c];
                if (h >= rows) continue;
                if (grid[h][c] === 1) {
                    seq.push(String(c + 1));
                    heights[c] += 1;
                    placed += 1;
                    progressed = true;
                }
            }
            if (!progressed) break;
        }
        return seq.join("");
    }

    function reconstructSequenceFromColumnStacks(stacks, c1, c2) {
        const total = c1 + c2;
        if (total === 0) return "";

        function reconstruct(firstColor) {
            const secondColor = firstColor === 1 ? 2 : 1;
            const p1Count = firstColor === 1 ? c1 : c2;
            const p2Count = firstColor === 1 ? c2 : c1;
            if (!(p1Count === p2Count || p1Count === p2Count + 1)) return null;

            const tops = stacks.map((s) => s.length);
            const lastPlayer = total % 2 === 1 ? 1 : 2;
            const fail = new Set();

            function tokenForPlayer(player) {
                return player === 1 ? firstColor : secondColor;
            }

            function key(turn) {
                return `${turn}|${tops.join(',')}`;
            }

            function dfs(turn) {
                let remaining = 0;
                for (const t of tops) remaining += t;
                if (remaining === 0) return [];

                const k = key(turn);
                if (fail.has(k)) return null;

                const needed = tokenForPlayer(turn);
                for (let col = 0; col < 7; col++) {
                    const t = tops[col];
                    if (t <= 0) continue;
                    if (stacks[col][t - 1] !== needed) continue;

                    tops[col] -= 1;
                    const tail = dfs(turn === 1 ? 2 : 1);
                    tops[col] += 1;
                    if (tail) {
                        tail.push(col);
                        return tail;
                    }
                }
                fail.add(k);
                return null;
            }

            const rev = dfs(lastPlayer);
            if (!rev) return null;
            return rev.map((c) => String(c + 1)).join("");
        }

        const seqA = reconstruct(1);
        if (seqA && isPlausibleSequence(seqA)) return seqA;

        const seqB = reconstruct(2);
        if (seqB && isPlausibleSequence(seqB)) return seqB;
        return null;
    }

    function nearestIndex(v, centers) {
        let best = 0;
        let bestDist = Infinity;
        for (let i = 0; i < centers.length; i++) {
            const d = Math.abs(v - centers[i]);
            if (d < bestDist) {
                bestDist = d;
                best = i;
            }
        }
        return best;
    }

    function parseSequenceFromGridBoardDirect() {
        function isTransientTokenClass(cls) {
            // Ignore hover/preview/highlight overlays; they are not committed moves.
            return (
                cls.includes('highlight') ||
                cls.includes('hover') ||
                cls.includes('preview') ||
                cls.includes('ghost') ||
                cls.includes('candidate') ||
                cls.includes('possible') ||
                cls.includes('hint')
            );
        }

        const items = Array.from(document.querySelectorAll('.grid-item'))
            .map((el) => {
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) return null;

                const ownCls = (el.getAttribute('class') || '').toLowerCase();
                const child = el.querySelector('circle, [class*="circle-"], [class*="empty-slot"]');
                const childCls = child ? (child.getAttribute('class') || '').toLowerCase() : '';
                const parentCls = child && child.parentElement ? (child.parentElement.getAttribute('class') || '').toLowerCase() : '';
                const grandCls = child && child.parentElement && child.parentElement.parentElement
                    ? (child.parentElement.parentElement.getAttribute('class') || '').toLowerCase()
                    : '';
                const cls = `${ownCls} ${childCls} ${parentCls} ${grandCls}`;

                let state = 0;
                if (isTransientTokenClass(cls)) state = 0;
                else if (cls.includes('circle-light')) state = 1;
                else if (cls.includes('circle-dark')) state = 2;
                else if (cls.includes('empty-slot')) state = 0;

                return {
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    state,
                };
            })
            .filter(Boolean);

        if (items.length < 42) return null;

        const unique = [];
        const seen = new Set();
        for (const s of items) {
            const key = `${Math.round(s.x)}:${Math.round(s.y)}`;
            if (seen.has(key)) continue;
            seen.add(key);
            unique.push(s);
        }
        if (unique.length < 42) return null;

        const boardSlots = unique.slice(0, 42);
        const xs = boardSlots.map((s) => s.x).sort((a, b) => a - b);
        const ys = boardSlots.map((s) => s.y).sort((a, b) => a - b);
        const colCenters = [];
        const rowCenters = [];

        for (let c = 0; c < 7; c++) {
            const chunk = xs.slice(c * 6, c * 6 + 6);
            if (!chunk.length) return null;
            colCenters.push(chunk.reduce((a, b) => a + b, 0) / chunk.length);
        }
        for (let r = 0; r < 6; r++) {
            const chunk = ys.slice(r * 7, r * 7 + 7);
            if (!chunk.length) return null;
            rowCenters.push(chunk.reduce((a, b) => a + b, 0) / chunk.length);
        }

        const grid = Array.from({ length: 6 }, () => Array(7).fill(0));
        const occupancy = Array.from({ length: 6 }, () => Array(7).fill(0));
        for (const s of boardSlots) {
            const col = nearestIndex(s.x, colCenters);
            const row = nearestIndex(s.y, rowCenters);
            if (s.state !== 0 || occupancy[row][col] === 0) {
                grid[row][col] = s.state;
            }
            occupancy[row][col] += 1;
        }

        const stacks = [];
        let c1 = 0;
        let c2 = 0;
        for (let col = 0; col < 7; col++) {
            const stack = [];
            let seenEmpty = false;
            for (let row = 5; row >= 0; row--) {
                const v = grid[row][col];
                if (v === 0) {
                    seenEmpty = true;
                    continue;
                }
                if (seenEmpty) return null;
                stack.push(v);
                if (v === 1) c1++;
                if (v === 2) c2++;
            }
            stacks.push(stack);
        }

        state.lastGridColumnCounts = stacks.map((s) => s.length);

        return reconstructSequenceFromColumnStacks(stacks, c1, c2);
    }

    function clickColumnFromGrid(col) {
        if (!Number.isInteger(col) || col < 0 || col >= 7) return false;

        const cells = Array.from(document.querySelectorAll('.grid-item'))
            .map((el) => {
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) return null;
                return {
                    el,
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                };
            })
            .filter(Boolean);

        if (cells.length < 42) return false;

        const unique = [];
        const seen = new Set();
        for (const s of cells) {
            const key = `${Math.round(s.x)}:${Math.round(s.y)}`;
            if (seen.has(key)) continue;
            seen.add(key);
            unique.push(s);
        }
        if (unique.length < 42) return false;

        const boardSlots = unique.slice(0, 42);
        const xs = boardSlots.map((s) => s.x).sort((a, b) => a - b);
        const colCenters = [];
        for (let c = 0; c < 7; c++) {
            const chunk = xs.slice(c * 6, c * 6 + 6);
            if (!chunk.length) return false;
            colCenters.push(chunk.reduce((a, b) => a + b, 0) / chunk.length);
        }

        const inCol = boardSlots.filter((s) => nearestIndex(s.x, colCenters) === col);
        if (!inCol.length) return false;
        inCol.sort((a, b) => a.y - b.y); // top-most slot for this column
        const target = inCol[0].el;

        try {
            target.click();
            return true;
        } catch (_) {
            return false;
        }
    }

    function collectFromStructuredValue(value, out, keyContext = "") {
        if (value == null) return;

        if (typeof value === "string") {
            if (hasMoveHint(keyContext) && isPlausibleSequence(value)) out.push(value);
            return;
        }

        if (Array.isArray(value)) {
            if (value.length >= 1 && value.length <= 42 && value.every((x) => Number.isInteger(x) && x >= 1 && x <= 7)) {
                const seq = value.join("");
                if (isPlausibleSequence(seq)) out.push(seq);
            }
            for (const x of value) collectFromStructuredValue(x, out, keyContext);
            return;
        }

        if (typeof value === "object") {
            for (const k of Object.keys(value)) {
                const nextContext = keyContext ? keyContext + "." + k : k;
                collectFromStructuredValue(value[k], out, nextContext);
            }
        }
    }

    function parseSequenceFromStorage() {
        const candidates = [];

        function processStorage(storage) {
            for (let i = 0; i < storage.length; i++) {
                const key = storage.key(i);
                if (!key) continue;
                const raw = storage.getItem(key);
                if (!raw) continue;
                if (!hasMoveHint(key)) continue;

                if (isPlausibleSequence(raw)) candidates.push(raw);
                try {
                    const parsed = JSON.parse(raw);
                    collectFromStructuredValue(parsed, candidates, key);
                } catch (_) {}
            }
        }

        try { processStorage(window.localStorage); } catch (_) {}
        try { processStorage(window.sessionStorage); } catch (_) {}

        if (!candidates.length) return null;
        const unique = Array.from(new Set(candidates)).filter(isPlausibleSequence);
        if (!unique.length) return null;
        unique.sort((a, b) => b.length - a.length);
        return unique[0];
    }

    window.__c4Bridge = {
        setSelectors(selectors) {
            state.selectors = Array.isArray(selectors) ? selectors : [];
            return true;
        },
        setSiteMode(mode) {
            state.siteMode = (mode === 'papergames') ? 'papergames' : 'generic';
            return true;
        },
        boardRect,
        readMoveSequenceDetailed() {
            const fromCells = parseSequenceFromCells();
            if (fromCells !== null) return { sequence: fromCells, source: "cells" };

            const fromGrid = parseSequenceFromGridBoardDirect();
            if (fromGrid !== null) return { sequence: fromGrid, source: "grid" };

            if (state.siteMode === 'papergames') {
                return { sequence: null, source: null };
            }

            const fromStorage = parseSequenceFromStorage();
            if (fromStorage !== null) return { sequence: fromStorage, source: "storage" };

            return { sequence: null, source: null };
        },
        readMoveSequence() {
            const d = this.readMoveSequenceDetailed();
            return d.sequence;
        },
        readGridColumnCounts() {
            // Refresh cached counts from current grid if possible.
            parseSequenceFromGridBoardDirect();
            return state.lastGridColumnCounts;
        },
        clickColumnDom(col) {
            return clickColumnFromGrid(col);
        },
    };
})();
"""

AD_HOST_KEYWORDS = [
    "doubleclick",
    "googlesyndication",
    "googleadservices",
    "adservice",
    "adsystem",
    "taboola",
    "outbrain",
    "adnxs",
    "criteo",
    "scorecardresearch",
]

# Time to wait for board state to reflect an auto-played move before retrying once.
AUTO_COMMIT_TIMEOUT_SEC = 2.0
# Absolute limit for waiting on one pending move before giving up and re-evaluating.
PENDING_MAX_WAIT_SEC = 7.0
# If a move could not be confirmed, avoid immediately replaying on unchanged sequence.
FAILED_SEQUENCE_COOLDOWN_SEC = 5.0
# Number of consecutive empty grid snapshots required before treating as reset.
EMPTY_RESET_STREAK = 5
SLOW_SOLVE_THRESHOLD_SEC = 10.0
SLOW_SOLVE_LOG_PATH = "data/slow_solve_prefixes.log"
# If post-game controls do not appear in time, reload to lobby and requeue.
# Set to 0 to disable fallback; users can opt in via CLI.
POST_GAME_RELOAD_TIMEOUT_SEC = 0.0
# If lobby/live-room URL remains idle this long after a confirmed queue click,
# retry queueing. Papergames matchmaking can take up to ~30s.
QUEUE_RETRY_IDLE_SEC = 30.0
# After a queue click, allow URL-gating bypass only when strong in-game
# signals are present for at least this long.
QUEUE_ATTACH_SIGNAL_GRACE_SEC = 1.5
# Minimum gap between queue click attempts.
QUEUE_CLICK_RETRY_GAP_SEC = 1.5
# Minimum gap between post-game leave/rematch click attempts.
POST_GAME_ACTION_RETRY_GAP_SEC = 0.6
# Delay before attempting post-game leave/rematch actions.
POST_GAME_ACTION_DELAY_SEC = 5.0
STATS_JSON_PATH = "data/bridge_stats.json"
STATS_CSV_PATH = "data/bridge_match_history.csv"


@dataclass
class TargetConfig:
    board_selectors: list[str]



def load_target_config(target: str, config_path: Optional[str]) -> TargetConfig:
    default_selectors = [
        "canvas",
        "[class*='board']",
        "[id*='board']",
        "[class*='connect']",
        "[id*='connect']",
        ".grid-item",
    ]

    if target == "papergames":
        default_selectors = [
            "#game",
            ".grid-item",
            "svg",
            "canvas",
            "[class*='board']",
        ]

    if config_path:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        selectors = data.get("board_selectors")
        if not isinstance(selectors, list) or not all(isinstance(x, str) for x in selectors):
            raise ValueError("config JSON must include string array: board_selectors")
        return TargetConfig(board_selectors=selectors)

    return TargetConfig(board_selectors=default_selectors)


class SolverClient:
    def __init__(self, solver_path: str, weak: bool = False) -> None:
        cmd = [solver_path]
        if weak:
            cmd.append("-w")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("failed to start solver subprocess")

    def best_move(self, sequence: str) -> int:
        if self.proc.poll() is not None:
            stderr = ""
            if self.proc.stderr is not None:
                try:
                    stderr = self.proc.stderr.read().strip()
                except Exception:
                    stderr = ""
            raise RuntimeError(f"solver exited unexpectedly. {stderr}")

        query = f"{sequence}?\n"
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        self.proc.stdin.write(query)
        self.proc.stdin.flush()

        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("solver returned no output")

        m = re.search(r"(\d+)", line)
        if not m:
            raise RuntimeError(f"unexpected solver response: {line.strip()}")

        col_one_based = int(m.group(1))
        if not 1 <= col_one_based <= 7:
            raise RuntimeError(f"solver returned invalid column: {col_one_based}")
        return col_one_based - 1

    def status(self, sequence: str) -> str:
        if self.proc.poll() is not None:
            raise RuntimeError("solver exited unexpectedly")

        query = f"{sequence}!\n"
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        self.proc.stdin.write(query)
        self.proc.stdin.flush()

        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("solver returned no status output")

        status = line.strip().lower()
        if status not in {"ongoing", "win1", "win2", "draw", "invalid"}:
            raise RuntimeError(f"unexpected solver status response: {status}")
        return status

    def close(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=1.0)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    default_solver = os.path.join(os.path.dirname(__file__), "..", "solver")

    parser = argparse.ArgumentParser(description="Bridge browser Connect4 with local solver")
    parser.add_argument("--url", default="https://papergames.io/en/connect4", help="Game URL")
    parser.add_argument(
        "--site-mode",
        choices=["auto", "generic", "papergames"],
        default="auto",
        help="Detection profile: auto, generic, or papergames-specific",
    )
    parser.add_argument("--browser", choices=["chromium", "firefox"], default="chromium", help="Browser engine")
    parser.add_argument("--mode", choices=["observe", "assist", "auto"], default="observe")
    parser.add_argument(
        "--player",
        choices=["1", "2", "auto"],
        default="auto",
        help="Your side: 1 (first), 2 (second), or auto-detect at runtime",
    )
    parser.add_argument("--target", default="papergames", help="Target preset name")
    parser.add_argument("--config", default=None, help="Optional JSON config path")
    parser.add_argument("--solver", default=default_solver, help="Path to solver binary")
    parser.add_argument("--weak", action="store_true", help="Use weak solver mode (-w)")
    parser.add_argument("--poll-ms", type=int, default=700, help="Polling interval in ms")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--window-width", type=int, default=1920, help="Browser window width in pixels")
    parser.add_argument("--window-height", type=int, default=1080, help="Browser window height in pixels")
    parser.add_argument(
        "--block-ads",
        action="store_true",
        help="Block ad/tracker traffic (use --block-level to tune strictness)",
    )
    parser.add_argument(
        "--block-level",
        choices=["conservative", "aggressive"],
        default="conservative",
        help="Ad blocking strictness when --block-ads is enabled",
    )
    parser.add_argument(
        "--extension-dir",
        default=None,
        help="Path to unpacked Chromium extension directory (e.g., uBlock)",
    )
    parser.add_argument(
        "--user-data-dir",
        default=".pw-user-data",
        help="User data directory for persistent profile / extension state",
    )
    parser.add_argument(
        "--persistent-profile",
        action="store_true",
        help="Use persistent browser profile (keeps login/extensions/settings across runs)",
    )
    parser.add_argument(
        "--manual-fallback",
        action="store_true",
        help="Prompt for move sequence when page parser cannot decode board",
    )
    parser.add_argument(
        "--manual-input-mode",
        choices=["incremental", "full"],
        default="incremental",
        help="Manual fallback input style: incremental (opponent move only) or full sequence",
    )
    parser.add_argument(
        "--auto-rematch",
        action="store_true",
        help="In auto mode, click Rematch after a terminal state when available (default: leave room)",
    )
    parser.add_argument(
        "--post-game-reload-sec",
        type=float,
        default=POST_GAME_RELOAD_TIMEOUT_SEC,
        help="In auto papergames mode, reload lobby after this many seconds if post-game controls do not appear (0 disables)",
    )
    parser.add_argument(
        "--post-game-wait-sec",
        type=float,
        default=POST_GAME_ACTION_DELAY_SEC,
        help="In auto papergames mode, wait this many seconds on terminal page before leave/rematch actions",
    )
    parser.add_argument(
        "--auto-max-runtime-sec",
        type=float,
        default=0.0,
        help=(
            "In auto mode, maximum runtime before requesting drain; after current game "
            "resolves, bridge exits gracefully (0 disables)"
        ),
    )
    parser.add_argument(
        "--stats-json",
        default=STATS_JSON_PATH,
        help="Path to persistent bridge stats JSON summary",
    )
    parser.add_argument(
        "--stats-csv",
        default=STATS_CSV_PATH,
        help="Path to bridge match history CSV",
    )
    parser.add_argument(
        "--stats-reset",
        action="store_true",
        help="Reset persistent bridge stats files at startup",
    )
    parser.add_argument(
        "--our-username",
        default=None,
        help="Your papergames username to exclude from opponent capture",
    )
    parser.add_argument(
        "--debug-parse",
        action="store_true",
        help="Emit extra parser diagnostics on anomaly/recovery paths",
    )
    return parser.parse_args()


def _is_third_party(host: str, first_party_host: str) -> bool:
    if not host or not first_party_host:
        return False
    if host == first_party_host:
        return False
    return not host.endswith("." + first_party_host)


def should_block_request(url: str, resource_type: str, first_party_host: str, aggressive: bool) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    # Never block traffic types commonly used for game state and matchmaking.
    if resource_type in {"xhr", "fetch", "websocket", "eventsource"}:
        return False

    # Never block first-party document/navigation requests.
    if not _is_third_party(host, first_party_host) and resource_type == "document":
        return False

    is_known_ad_host = any(token in host for token in AD_HOST_KEYWORDS)
    if is_known_ad_host:
        return True

    # Conservative mode only blocks known ad/tracker hosts.
    if not aggressive:
        return False

    if _is_third_party(host, first_party_host):
        if resource_type in {"media", "font", "image"}:
            return True
        if resource_type in {"stylesheet"}:
            return True

    # Aggressive mode can also drop first-party heavy assets.
    if resource_type in {"media", "font"}:
        return True

    return False


def install_request_blocking(page, first_party_host: str, block_level: str) -> None:
    aggressive = block_level == "aggressive"

    def handler(route):
        req = route.request
        if should_block_request(req.url, req.resource_type, first_party_host, aggressive):
            route.abort()
            return
        route.continue_()

    page.route("**/*", handler)


def detect_player_from_sequence(move_sequence: str) -> int:
    while True:
        ans = input(
            "[bridge] Detect side: enter 1/2, or answer 'y' if it is your turn now (n if not): "
        ).strip().lower()

        if ans in {"1", "2"}:
            return int(ans)

        if ans in {"y", "n"}:
            even_len = (len(move_sequence) % 2 == 0)
            if ans == "y":
                return 1 if even_len else 2
            return 2 if even_len else 1

        print("[bridge] Please respond with 1, 2, y, or n")


def is_papergames_live_room_url(current_url: str) -> bool:
    """Detect papergames live-room routes robustly.

    Accepts locale variants (`en`, `en-us`), room codes with `_`/`-`, optional
    trailing path segments, and hash-routed paths.
    """
    try:
        parsed = urlparse(current_url)
    except Exception:
        return False

    candidate_paths: list[str] = []
    if parsed.path:
        candidate_paths.append(parsed.path)
    # Some SPA transitions can place route info in the URL fragment.
    if parsed.fragment and parsed.fragment.startswith("/"):
        candidate_paths.append(parsed.fragment)

    for raw_path in candidate_paths:
        path = raw_path.strip("/")
        if not path:
            continue

        parts = [p for p in path.split("/") if p]
        if len(parts) < 3:
            continue

        locale = parts[0].lower()
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", locale):
            continue
        if parts[1].lower() != "r":
            continue

        room_code = parts[2]
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,}", room_code):
            continue

        return True

    return False


def ensure_bridge_ready(page, selectors: list[str], site_mode: str) -> bool:
    """Ensure JS helpers are present after refresh/navigation."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
        ready = page.evaluate("() => typeof window.__c4Bridge !== 'undefined'")
        if ready:
            return True
        page.evaluate(BRIDGE_JS)
        page.evaluate("(sels) => window.__c4Bridge.setSelectors(sels)", selectors)
        page.evaluate("(mode) => window.__c4Bridge.setSiteMode(mode)", site_mode)
        return True
    except PlaywrightError:
        return False


def record_slow_solve(sequence: str, elapsed_sec: float, weak: bool) -> None:
    try:
        os.makedirs(os.path.dirname(SLOW_SOLVE_LOG_PATH), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(SLOW_SOLVE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{sequence}\t{elapsed_sec:.3f}\tweak={int(bool(weak))}\n")
    except Exception:
        # Slow-solve logging is best-effort only.
        pass


def main() -> int:
    args = parse_args()
    our_username = sanitize_username(args.our_username) if args.our_username else None

    stats = BridgeStats(args.stats_json, args.stats_csv)
    stats.load()
    if args.stats_reset:
        stats.reset()
        print("[bridge] Stats reset requested; cleared JSON/CSV history")

    if not os.path.exists(args.solver):
        print(f"[bridge] Solver not found at: {args.solver}")
        print("[bridge] Build with: make solver")
        return 1

    try:
        config = load_target_config(args.target, args.config)
    except Exception as exc:
        print(f"[bridge] Failed to load target config: {exc}")
        return 1

    solver = SolverClient(args.solver, weak=args.weak)

    host = urlparse(args.url).netloc.lower()
    if args.site_mode == "papergames":
        site_mode = "papergames"
    elif args.site_mode == "generic":
        site_mode = "generic"
    else:
        site_mode = "papergames" if "papergames.io" in host else "generic"

    if site_mode == "papergames" and our_username is None:
        print("[bridge] Tip: set --our-username to reduce self-as-opponent misclassification")

    print("[bridge] Starting browser bridge")
    print(f"[bridge] URL: {args.url}")
    print(f"[bridge] Browser: {args.browser}")
    print(f"[bridge] Site mode: {site_mode}")
    print(f"[bridge] Mode: {args.mode}, player={args.player}, weak={args.weak}")
    print(f"[bridge] Stats: {stats.summary_line()}")
    if args.debug_parse:
        print("[bridge] Parse debug logging enabled (anomaly paths only)")
    if args.mode == "auto":
        print("[bridge] Operator commands: pause | resume | status | wait <sec> | quit")
        if args.auto_max_runtime_sec and args.auto_max_runtime_sec > 0:
            print(f"[bridge] Auto max runtime: {float(args.auto_max_runtime_sec):.1f}s")
        if PromptSession is None:
            print("[bridge] Tip: install prompt_toolkit for a pinned command prompt (logs above, input below)")
    print("[bridge] Press Ctrl+C to stop")

    last_sequence: Optional[str] = None
    fixed_player: Optional[int] = None if args.player == "auto" else int(args.player)
    detected_player: Optional[int] = fixed_player
    manual_sequence: Optional[str] = None
    last_wait_log = 0.0
    match_active = False
    initial_storage_sequence: Optional[str] = None
    pending_expected_sequence: Optional[str] = None
    pending_base_sequence: Optional[str] = None
    pending_col: Optional[int] = None
    pending_retry_attempted = False
    pending_move_started_at: Optional[float] = None
    blocked_sequence: Optional[str] = None
    blocked_sequence_until = 0.0
    last_block_log = 0.0
    grid_seq_candidate: Optional[str] = None
    grid_seq_candidate_count = 0
    last_non_monotonic_log = 0.0
    inferred_move_candidate: Optional[str] = None
    inferred_move_candidate_count = 0
    last_grid_col_counts: Optional[list[int]] = None
    tracked_grid_sequence: Optional[str] = None
    last_suggested_col: Optional[int] = None
    last_suggested_at = 0.0
    empty_grid_streak = 0
    last_terminal_log = 0.0
    post_game_waiting_empty = False
    last_post_game_wait_log = 0.0
    last_solved_sequence: Optional[str] = None
    last_solved_col: Optional[int] = None
    last_logged_suggestion_seq: Optional[str] = None
    last_logged_suggestion_col: Optional[int] = None
    last_equivalent_log = 0.0
    auto_side_probe_started_at: Optional[float] = None
    post_game_mode = False
    post_game_started_at: Optional[float] = None
    seeking_new_match = False
    last_lifecycle_log = 0.0
    slow_logged_sequences: set[str] = set()
    queued_click_at: Optional[float] = None
    last_queue_click_attempt_at = 0.0
    last_post_game_action_attempt_at = 0.0
    current_opponent: Optional[str] = None
    game_result_recorded = False
    game_solve_total_sec = 0.0
    game_solve_samples = 0
    debug_event_last_log: dict[str, float] = {}
    auto_control_state = "running"  # running | draining | paused
    auto_runtime_start_at = time.time() if args.mode == "auto" else 0.0
    auto_runtime_limit_sec = (
        float(args.auto_max_runtime_sec)
        if args.mode == "auto" and args.auto_max_runtime_sec and args.auto_max_runtime_sec > 0
        else None
    )
    auto_runtime_hard_limit_sec = auto_runtime_limit_sec * 2.0 if auto_runtime_limit_sec is not None else None
    auto_runtime_timeout_triggered = False
    auto_runtime_hard_timeout_triggered = False
    auto_quit_after_drain = False
    exit_requested_after_drain = False
    timeout_summary_printed = False
    post_game_wait_sec_runtime = max(0.0, float(args.post_game_wait_sec))
    operator_cmd_queue: queue.Queue[str] = queue.Queue()
    operator_console_stop = threading.Event()
    operator_console_started = False
    last_observed_url = args.url

    def _snapshot_totals() -> tuple[int, int, int, int]:
        totals = stats.data.get("totals") if isinstance(stats.data, dict) else None
        if not isinstance(totals, dict):
            return (0, 0, 0, 0)
        wins = int(totals.get("wins", 0) or 0)
        losses = int(totals.get("losses", 0) or 0)
        draws = int(totals.get("draws", 0) or 0)
        games = int(totals.get("games", 0) or 0)
        return (wins, losses, draws, games)

    session_start_w, session_start_l, session_start_d, session_start_g = _snapshot_totals()

    def print_timeout_shutdown_summary(reason: str) -> None:
        nonlocal timeout_summary_printed
        if timeout_summary_printed:
            return
        timeout_summary_printed = True
        elapsed = max(0.0, time.time() - auto_runtime_start_at)
        end_w, end_l, end_d, end_g = _snapshot_totals()
        print(
            "[bridge] Timeout shutdown summary: "
            f"reason={reason} elapsed_sec={elapsed:.1f} "
            f"session W-L-D={end_w - session_start_w}-{end_l - session_start_l}-{end_d - session_start_d} "
            f"(games={end_g - session_start_g})"
        )

    def reset_runtime_for_next_match(
        *,
        seeking_new_match_value: Optional[bool] = None,
        post_game_waiting_empty_value: Optional[bool] = None,
    ) -> None:
        nonlocal match_active
        nonlocal post_game_mode
        nonlocal post_game_started_at
        nonlocal last_sequence
        nonlocal detected_player
        nonlocal manual_sequence
        nonlocal initial_storage_sequence
        nonlocal pending_expected_sequence
        nonlocal pending_base_sequence
        nonlocal pending_col
        nonlocal pending_retry_attempted
        nonlocal pending_move_started_at
        nonlocal blocked_sequence
        nonlocal blocked_sequence_until
        nonlocal grid_seq_candidate
        nonlocal grid_seq_candidate_count
        nonlocal inferred_move_candidate
        nonlocal inferred_move_candidate_count
        nonlocal last_grid_col_counts
        nonlocal tracked_grid_sequence
        nonlocal last_suggested_col
        nonlocal last_suggested_at
        nonlocal empty_grid_streak
        nonlocal last_solved_sequence
        nonlocal last_solved_col
        nonlocal last_logged_suggestion_seq
        nonlocal last_logged_suggestion_col
        nonlocal auto_side_probe_started_at
        nonlocal seeking_new_match
        nonlocal post_game_waiting_empty

        reset = RuntimeResetState.for_next_match(fixed_player)
        match_active = reset.match_active
        post_game_mode = reset.post_game_mode
        post_game_started_at = reset.post_game_started_at
        last_sequence = reset.last_sequence
        detected_player = reset.detected_player
        manual_sequence = reset.manual_sequence
        initial_storage_sequence = reset.initial_storage_sequence
        pending_expected_sequence = reset.pending_expected_sequence
        pending_base_sequence = reset.pending_base_sequence
        pending_col = reset.pending_col
        pending_retry_attempted = reset.pending_retry_attempted
        pending_move_started_at = reset.pending_move_started_at
        blocked_sequence = reset.blocked_sequence
        blocked_sequence_until = reset.blocked_sequence_until
        grid_seq_candidate = reset.grid_seq_candidate
        grid_seq_candidate_count = reset.grid_seq_candidate_count
        inferred_move_candidate = reset.inferred_move_candidate
        inferred_move_candidate_count = reset.inferred_move_candidate_count
        last_grid_col_counts = reset.last_grid_col_counts
        tracked_grid_sequence = reset.tracked_grid_sequence
        last_suggested_col = reset.last_suggested_col
        last_suggested_at = reset.last_suggested_at
        empty_grid_streak = reset.empty_grid_streak
        last_solved_sequence = reset.last_solved_sequence
        last_solved_col = reset.last_solved_col
        last_logged_suggestion_seq = reset.last_logged_suggestion_seq
        last_logged_suggestion_col = reset.last_logged_suggestion_col
        auto_side_probe_started_at = reset.auto_side_probe_started_at

        if seeking_new_match_value is not None:
            seeking_new_match = seeking_new_match_value
        if post_game_waiting_empty_value is not None:
            post_game_waiting_empty = post_game_waiting_empty_value

    def set_auto_control_paused(reason: str) -> None:
        nonlocal auto_control_state
        nonlocal post_game_mode
        nonlocal post_game_started_at
        nonlocal seeking_new_match

        auto_control_state = "paused"
        post_game_mode = False
        post_game_started_at = None
        seeking_new_match = False
        print(f"[bridge] Auto paused: {reason}")

    def on_game_resolved_maybe_pause() -> bool:
        nonlocal auto_control_state
        nonlocal exit_requested_after_drain
        if args.mode == "auto" and auto_control_state == "draining":
            set_auto_control_paused("current game resolved")
            if auto_quit_after_drain:
                print("[bridge] Auto runtime limit reached: current game resolved; quitting")
                exit_requested_after_drain = True
            return True
        return False

    def handle_terminal_transition(reason: str, context: str, sequence_len_hint: Optional[int] = None) -> bool:
        """Record terminal outcome, clear pending move state, and enter post-game flow.

        Returns True when caller should continue loop processing.
        """
        nonlocal game_result_recorded
        nonlocal post_game_mode
        nonlocal post_game_started_at
        nonlocal pending_expected_sequence
        nonlocal pending_base_sequence
        nonlocal pending_col
        nonlocal pending_retry_attempted
        nonlocal pending_move_started_at

        print(f"[bridge] {context}: {reason}")
        if not game_result_recorded:
            mapped = result_from_terminal_reason(reason, detected_player)
            if mapped is not None:
                stats.record_game(
                    mapped,
                    detected_player,
                    current_opponent,
                    sequence_len_hint if sequence_len_hint is not None else len(last_sequence or ""),
                    game_solve_samples,
                    game_solve_total_sec,
                )
                game_result_recorded = True
                print(f"[bridge] Stats updated: {stats.summary_line()}")

        pending_expected_sequence = None
        pending_base_sequence = None
        pending_col = None
        pending_retry_attempted = False
        pending_move_started_at = None

        if on_game_resolved_maybe_pause():
            return True

        post_game_mode = True
        post_game_started_at = time.time()
        return True

    def process_operator_command(cmd: str) -> bool:
        """Process one runtime operator command; returns True when bridge should exit."""
        nonlocal auto_control_state
        nonlocal post_game_wait_sec_runtime
        cmd = cmd.strip().lower()
        if not cmd:
            return False

        if cmd in {"quit", "q", "exit"}:
            print("[bridge] Quit requested by operator")
            return True

        if cmd in {"help", "h", "?"}:
            print("[bridge] Commands: pause | resume | status | wait <sec> | quit")
            return False

        parts = cmd.split()
        if parts and parts[0] in {"wait", "postwait", "post-game-wait"}:
            if len(parts) == 1:
                print(f"[bridge] Current post-game wait: {post_game_wait_sec_runtime:.1f}s")
                return False
            if len(parts) != 2:
                print("[bridge] Usage: wait <seconds>")
                return False
            try:
                new_wait = float(parts[1])
            except ValueError:
                print("[bridge] wait expects a number, e.g. 'wait 6.5'")
                return False
            if new_wait < 0:
                print("[bridge] wait must be >= 0")
                return False
            post_game_wait_sec_runtime = new_wait
            print(f"[bridge] Updated post-game wait to {post_game_wait_sec_runtime:.1f}s")
            return False

        if cmd in {"status", "s"}:
            in_live_room = site_mode == "papergames" and is_papergames_live_room_url(last_observed_url)
            runtime_info = ""
            if auto_runtime_limit_sec is not None:
                elapsed = max(0.0, time.time() - auto_runtime_start_at)
                remaining = max(0.0, auto_runtime_limit_sec - elapsed)
                hard_remaining = (
                    max(0.0, auto_runtime_hard_limit_sec - elapsed)
                    if auto_runtime_hard_limit_sec is not None
                    else 0.0
                )
                runtime_info = (
                    f" auto_runtime_limit_sec={auto_runtime_limit_sec:.1f}"
                    f" auto_runtime_remaining_sec={remaining:.1f}"
                    f" auto_runtime_hard_remaining_sec={hard_remaining:.1f}"
                )
            print(
                "[bridge] Control status: "
                f"state={auto_control_state} match_active={match_active} "
                f"post_game_mode={post_game_mode} seeking_new_match={seeking_new_match} "
                f"live_room_url={in_live_room} "
                f"post_game_wait_sec={post_game_wait_sec_runtime:.1f}"
                f"{runtime_info}"
            )
            return False

        if cmd in {"pause", "p", "stop"}:
            if auto_control_state == "paused":
                print("[bridge] Already paused")
                return False
            in_live_room = site_mode == "papergames" and is_papergames_live_room_url(last_observed_url)
            if post_game_mode:
                set_auto_control_paused("paused immediately")
                return False
            if not match_active and not in_live_room:
                set_auto_control_paused("paused immediately")
                return False

            auto_control_state = "draining"
            print("[bridge] Drain requested: will pause after current game resolves")
            return False

        if cmd in {"resume", "r", "run"}:
            if auto_control_state == "running":
                print("[bridge] Already running")
                return False
            auto_control_state = "running"
            print("[bridge] Auto resumed")
            return False

        print(f"[bridge] Unknown command: {cmd}. Try: pause | resume | status | wait <sec> | quit")
        return False

    def handle_operator_command() -> bool:
        """Handle runtime operator commands; returns True when bridge should exit."""
        if args.mode != "auto":
            return False

        while True:
            try:
                queued_cmd = operator_cmd_queue.get_nowait()
            except queue.Empty:
                break
            if process_operator_command(queued_cmd):
                return True

        # When the prompt_toolkit command line is active, it owns stdin.
        # Do not race it with direct readline polling.
        if operator_console_started:
            return False

        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0.0)
        except Exception:
            return False

        if not ready:
            return False

        raw = sys.stdin.readline()
        if raw is None:
            return False

        return process_operator_command(raw)

    try:
        with sync_playwright() as p:
            browser = None
            browser_type = p.chromium if args.browser == "chromium" else p.firefox

            if args.extension_dir:
                if args.browser != "chromium":
                    print("[bridge] --extension-dir is only supported with --browser chromium")
                    return 1

                ext_dir = os.path.abspath(args.extension_dir)
                if not os.path.isdir(ext_dir):
                    print(f"[bridge] Extension dir not found: {ext_dir}")
                    return 1
                if args.headless:
                    print("[bridge] Extension mode requires headed Chromium; ignoring --headless")

                user_data_dir = os.path.abspath(args.user_data_dir)
                os.makedirs(user_data_dir, exist_ok=True)
                context = p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=False,
                    args=[
                        f"--window-size={args.window_width},{args.window_height}",
                        f"--disable-extensions-except={ext_dir}",
                        f"--load-extension={ext_dir}",
                    ],
                    viewport={"width": args.window_width, "height": args.window_height},
                )
                page = context.pages[0] if context.pages else context.new_page()
            elif args.persistent_profile:
                user_data_dir = os.path.abspath(args.user_data_dir)
                os.makedirs(user_data_dir, exist_ok=True)
                launch_kwargs = {
                    "headless": args.headless,
                    "viewport": {"width": args.window_width, "height": args.window_height},
                }
                if args.browser == "chromium":
                    launch_kwargs["args"] = [f"--window-size={args.window_width},{args.window_height}"]

                try:
                    context = browser_type.launch_persistent_context(user_data_dir, **launch_kwargs)
                    page = context.pages[0] if context.pages else context.new_page()
                except PlaywrightError as exc:
                    # Common case: Firefox profile is already in use by another running instance.
                    print(f"[bridge] Persistent profile launch failed: {exc}")
                    print("[bridge] Falling back to non-persistent browser context")
                    launch_kwargs = {"headless": args.headless}
                    if args.browser == "chromium":
                        launch_kwargs["args"] = [f"--window-size={args.window_width},{args.window_height}"]
                    browser = browser_type.launch(**launch_kwargs)
                    context = browser.new_context(viewport={"width": args.window_width, "height": args.window_height})
                    page = context.new_page()
            else:
                launch_kwargs = {"headless": args.headless}
                if args.browser == "chromium":
                    launch_kwargs["args"] = [f"--window-size={args.window_width},{args.window_height}"]

                browser = browser_type.launch(**launch_kwargs)
                context = browser.new_context(viewport={"width": args.window_width, "height": args.window_height})
                page = context.new_page()

            blocking_active = False
            first_party_host = urlparse(args.url).netloc.lower()

            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)

            # Inject helper bridge and selectors.
            page.evaluate(BRIDGE_JS)
            page.evaluate("(selectors) => window.__c4Bridge.setSelectors(selectors)", config.board_selectors)
            page.evaluate("(mode) => window.__c4Bridge.setSiteMode(mode)", site_mode)

            def start_operator_console() -> None:
                nonlocal operator_console_started
                if operator_console_started:
                    return
                if args.mode != "auto":
                    return
                if PromptSession is None:
                    return
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    return

                def operator_prompt_loop() -> None:
                    session = PromptSession("[bridge cmd] ")
                    with patch_stdout():
                        while not operator_console_stop.is_set():
                            try:
                                raw = session.prompt()
                            except KeyboardInterrupt:
                                # Keep Ctrl+C as a fast stop signal when prompt mode owns stdin.
                                operator_cmd_queue.put("quit")
                                break
                            except EOFError:
                                break
                            if raw is None:
                                continue
                            cmd = raw.strip()
                            if not cmd:
                                continue
                            operator_cmd_queue.put(cmd)

                t = threading.Thread(target=operator_prompt_loop, daemon=True)
                t.start()
                operator_console_started = True
                print("[bridge] Pinned operator prompt enabled: use '[bridge cmd]' line for commands")

            start_operator_console()

            def debug_log_event(
                event: str,
                payload: Optional[dict] = None,
                min_interval_sec: float = 2.0,
            ) -> None:
                if not args.debug_parse:
                    return
                now = time.time()
                last = debug_event_last_log.get(event, 0.0)
                if now - last < min_interval_sec:
                    return
                debug_event_last_log[event] = now

                body: dict = {"event": event}
                if isinstance(payload, dict):
                    body["payload"] = payload
                print(f"[bridge][debug] {json.dumps(body, sort_keys=True)}")

            def debug_log_parse_snapshot(
                event: str,
                payload: Optional[dict] = None,
                min_interval_sec: float = 2.0,
            ) -> None:
                if not args.debug_parse:
                    return
                snap_payload = dict(payload) if isinstance(payload, dict) else {}
                try:
                    snap_payload["snapshot"] = page.evaluate(
                        """
                        () => {
                          const raw = (window.__c4Bridge && window.__c4Bridge.readMoveSequenceDetailed)
                            ? window.__c4Bridge.readMoveSequenceDetailed()
                            : null;
                          const counts = (window.__c4Bridge && window.__c4Bridge.readGridColumnCounts)
                            ? window.__c4Bridge.readGridColumnCounts()
                            : null;
                          return { raw, counts };
                        }
                        """
                    )
                except PlaywrightError as exc:
                    snap_payload["snapshot_error"] = str(exc)
                debug_log_event(event, snap_payload, min_interval_sec=min_interval_sec)

            def debug_log_opponent_probe(min_interval_sec: float = 8.0) -> Optional[str]:
                try:
                    raw = page.evaluate(
                        r"""
                        () => {
                          const pull = (s) => {
                            if (!s) return null;
                            const t = String(s).trim();
                            return t.length ? t : null;
                          };

                          const labelRegex = /\bopponent\b\s*[:\-]?\s*([A-Za-z0-9_.\- ]{2,32})/i;
                          const body = document.body?.innerText || '';
                          const mBody = body.match(labelRegex);
                          const labelCandidate = (mBody && mBody[1]) ? pull(mBody[1]) : null;

                                                    const addUnique = (arr, v) => {
                                                        if (!v) return;
                                                        if (!arr.includes(v)) arr.push(v);
                                                    };

                                                    const opponentProfiles = [];
                                                    const opponentAnchors = Array.from(
                                                        document.querySelectorAll('[class*="opponent" i], [id*="opponent" i], [aria-label*="opponent" i]')
                                                    );
                                                    const nameSel = '[appprofileopener], [appProfileOpener], [class*="profile" i], [class*="username" i], [data-username], [title]';
                                                    for (const anchor of opponentAnchors) {
                                                        const local = Array.from(anchor.querySelectorAll(nameSel));
                                                        for (const el of local) {
                                                            const txt = pull(el.innerText || el.textContent || el.getAttribute('data-username') || el.getAttribute('title') || '');
                                                            addUnique(opponentProfiles, txt);
                                                        }

                                                        const parent = anchor.parentElement;
                                                        if (parent) {
                                                            const nearby = Array.from(parent.querySelectorAll(nameSel));
                                                            for (const el of nearby) {
                                                                const txt = pull(el.innerText || el.textContent || el.getAttribute('data-username') || el.getAttribute('title') || '');
                                                                addUnique(opponentProfiles, txt);
                                                            }
                                                        }
                                                    }

                                                    const roomProfiles = [];
                                                    const boardCell = document.querySelector('.grid-item');
                                                    const gameRoot = document.querySelector('#game') ||
                                                        (boardCell ? boardCell.closest('#game, [class*="game" i], [id*="game" i], [class*="room" i], [id*="room" i]') : null);
                                                    if (gameRoot) {
                                                        const roomNameEls = Array.from(gameRoot.querySelectorAll(nameSel));
                                                        for (const el of roomNameEls) {
                                                            const txt = pull(el.innerText || el.textContent || el.getAttribute('data-username') || el.getAttribute('title') || '');
                                                            addUnique(roomProfiles, txt);
                                                        }
                                                    }

                                                    const roomPlayerProfiles = [];
                                                    const roomPlayers = document.querySelector('app-room-players');
                                                    if (roomPlayers) {
                                                        const profileEls = Array.from(
                                                            roomPlayers.querySelectorAll(
                                                                'span[appprofileopener], span[appProfileOpener], [appprofileopener], [appProfileOpener]'
                                                            )
                                                        );
                                                        for (const el of profileEls) {
                                                            const txt = pull(el.innerText || el.textContent || el.getAttribute('data-username') || el.getAttribute('title') || '');
                                                            addUnique(roomPlayerProfiles, txt);
                                                        }
                                                    }

                                                    const boardNearbyProfiles = [];
                                                    if (boardCell) {
                                                        const boardRect = boardCell.getBoundingClientRect();
                                                        const boardLeft = boardRect.left;
                                                        const boardRight = boardRect.right;
                                                        const boardTop = boardRect.top;

                                                        const nearNameEls = Array.from(
                                                            document.querySelectorAll(
                                                                '[appprofileopener], [appProfileOpener], span.text-truncate.cursor-pointer, [class*="text-truncate" i][class*="cursor-pointer" i], [class*="username" i], [data-username]'
                                                            )
                                                        );

                                                        for (const el of nearNameEls) {
                                                            const rect = el.getBoundingClientRect();
                                                            if (rect.width <= 0 || rect.height <= 0) continue;
                                                            const cx = rect.left + rect.width / 2;
                                                            const cy = rect.top + rect.height / 2;
                                                            const withinX = cx >= (boardLeft - 220) && cx <= (boardRight + 220);
                                                            const aboveBoard = cy <= (boardTop + 120);
                                                            if (!withinX || !aboveBoard) continue;

                                                            const txt = pull(el.innerText || el.textContent || el.getAttribute('data-username') || el.getAttribute('title') || '');
                                                            addUnique(boardNearbyProfiles, txt);
                                                        }
                                                    }

                                                    return { labelCandidate, opponentProfiles, roomProfiles, roomPlayerProfiles, boardNearbyProfiles };
                        }
                        """
                    )
                except PlaywrightError as exc:
                    debug_log_event("opponent_parse_probe_failed", {"error": str(exc)}, min_interval_sec=min_interval_sec)
                    return None

                label_candidate = None
                opponent_profile_candidates: list[str] = []
                room_profile_candidates: list[str] = []
                room_player_profile_candidates: list[str] = []
                board_nearby_profile_candidates: list[str] = []
                if isinstance(raw, dict):
                    label_candidate = raw.get("labelCandidate")
                    raw_opp_profiles = raw.get("opponentProfiles")
                    if isinstance(raw_opp_profiles, list):
                        opponent_profile_candidates = [p for p in raw_opp_profiles if isinstance(p, str)]
                    raw_room_profiles = raw.get("roomProfiles")
                    if isinstance(raw_room_profiles, list):
                        room_profile_candidates = [p for p in raw_room_profiles if isinstance(p, str)]
                    raw_room_player_profiles = raw.get("roomPlayerProfiles")
                    if isinstance(raw_room_player_profiles, list):
                        room_player_profile_candidates = [p for p in raw_room_player_profiles if isinstance(p, str)]
                    raw_board_profiles = raw.get("boardNearbyProfiles")
                    if isinstance(raw_board_profiles, list):
                        board_nearby_profile_candidates = [p for p in raw_board_profiles if isinstance(p, str)]

                sanitized_label = sanitize_username(label_candidate) if isinstance(label_candidate, str) else None
                if sanitized_label is not None and usernames_equivalent(sanitized_label, our_username):
                    sanitized_label = None

                sanitized_opponent_profiles: list[str] = []
                for name in opponent_profile_candidates:
                    cleaned = sanitize_username(name)
                    if cleaned is None:
                        continue
                    if usernames_equivalent(cleaned, our_username):
                        continue
                    if cleaned in sanitized_opponent_profiles:
                        continue
                    sanitized_opponent_profiles.append(cleaned)

                sanitized_room_profiles: list[str] = []
                for name in room_profile_candidates:
                    cleaned = sanitize_username(name)
                    if cleaned is None:
                        continue
                    if usernames_equivalent(cleaned, our_username):
                        continue
                    if cleaned in sanitized_room_profiles:
                        continue
                    sanitized_room_profiles.append(cleaned)

                sanitized_room_player_profiles: list[str] = []
                for name in room_player_profile_candidates:
                    cleaned = sanitize_username(name)
                    if cleaned is None:
                        continue
                    if usernames_equivalent(cleaned, our_username):
                        continue
                    if cleaned in sanitized_room_player_profiles:
                        continue
                    sanitized_room_player_profiles.append(cleaned)

                sanitized_board_nearby_profiles: list[str] = []
                for name in board_nearby_profile_candidates:
                    cleaned = sanitize_username(name)
                    if cleaned is None:
                        continue
                    if usernames_equivalent(cleaned, our_username):
                        continue
                    if cleaned in sanitized_board_nearby_profiles:
                        continue
                    sanitized_board_nearby_profiles.append(cleaned)

                fallback_candidate: Optional[str] = None
                # Use only higher-confidence sources for fallback detection.
                # Do not use body label regex fallback here; it is too noisy on some pages.
                for c in (
                    sanitized_room_player_profiles
                    + sanitized_board_nearby_profiles
                    + sanitized_opponent_profiles
                    + sanitized_room_profiles
                ):
                    if isinstance(c, str) and c:
                        fallback_candidate = c
                        break

                debug_log_event(
                    "opponent_parse_miss",
                    {
                        "label_candidate": label_candidate,
                        "label_sanitized": sanitized_label,
                        "opponent_profile_candidates": opponent_profile_candidates,
                        "opponent_profiles_sanitized": sanitized_opponent_profiles,
                        "room_profile_candidates": room_profile_candidates,
                        "room_profiles_sanitized": sanitized_room_profiles,
                        "room_player_profile_candidates": room_player_profile_candidates,
                        "room_player_profiles_sanitized": sanitized_room_player_profiles,
                        "board_nearby_profile_candidates": board_nearby_profile_candidates,
                        "board_nearby_profiles_sanitized": sanitized_board_nearby_profiles,
                    },
                    min_interval_sec=min_interval_sec,
                )
                return fallback_candidate

            while True:
                if page.is_closed():
                    print("[bridge] Page was closed; stopping bridge")
                    return 0

                if exit_requested_after_drain:
                    print_timeout_shutdown_summary("soft-timeout-drain-complete")
                    return 0

                try:
                    last_observed_url = page.url
                except Exception:
                    pass

                if handle_operator_command():
                    return 0

                if (
                    args.mode == "auto"
                    and auto_runtime_hard_limit_sec is not None
                    and not auto_runtime_hard_timeout_triggered
                ):
                    now = time.time()
                    elapsed = now - auto_runtime_start_at
                    if elapsed >= auto_runtime_hard_limit_sec:
                        auto_runtime_hard_timeout_triggered = True
                        print(
                            "[bridge] Auto hard runtime limit reached "
                            f"({elapsed:.1f}s): force quitting"
                        )
                        print_timeout_shutdown_summary("hard-timeout-force-quit")
                        return 0

                if (
                    args.mode == "auto"
                    and auto_runtime_limit_sec is not None
                    and not auto_runtime_timeout_triggered
                ):
                    now = time.time()
                    elapsed = now - auto_runtime_start_at
                    if elapsed >= auto_runtime_limit_sec:
                        auto_runtime_timeout_triggered = True
                        auto_quit_after_drain = True

                        in_live_room = (
                            site_mode == "papergames"
                            and is_papergames_live_room_url(last_observed_url)
                        )

                        if post_game_mode:
                            print(
                                "[bridge] Auto runtime limit reached "
                                f"({elapsed:.1f}s): game already resolved; quitting"
                            )
                            print_timeout_shutdown_summary("soft-timeout-post-game")
                            return 0

                        if auto_control_state == "paused":
                            print(
                                "[bridge] Auto runtime limit reached "
                                f"({elapsed:.1f}s) while paused; quitting"
                            )
                            print_timeout_shutdown_summary("soft-timeout-paused")
                            return 0

                        if not match_active and not in_live_room:
                            print(
                                "[bridge] Auto runtime limit reached "
                                f"({elapsed:.1f}s) with no active game; quitting"
                            )
                            print_timeout_shutdown_summary("soft-timeout-no-active-game")
                            return 0

                        auto_control_state = "draining"
                        print(
                            "[bridge] Auto runtime limit reached "
                            f"({elapsed:.1f}s): drain requested; will quit after current game resolves"
                        )

                if args.mode == "auto" and auto_control_state == "paused":
                    now = time.time()
                    if now - last_wait_log >= 5.0:
                        print("[bridge] Auto paused; waiting for 'resume' or 'quit'")
                        last_wait_log = now
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if args.mode == "auto" and site_mode == "papergames" and post_game_mode:
                    now = time.time()
                    if post_game_started_at is None:
                        post_game_started_at = now
                    post_game_elapsed = now - post_game_started_at

                    if post_game_elapsed < post_game_wait_sec_runtime:
                        if now - last_lifecycle_log >= 2.0:
                            print(
                                "[bridge] Terminal wait before post-game action... "
                                f"({post_game_elapsed:.1f}/{post_game_wait_sec_runtime:.1f}s)"
                            )
                            last_lifecycle_log = now
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    # Papergames may auto-redirect to lobby after a short delay.
                    # If that happens, stop waiting for in-game post-game controls
                    # and switch directly to the matchmaking flow.
                    if in_lobby_url(page.url, args.url) and not has_in_game_ui(page):
                        print("[bridge] Post-game redirect to lobby detected; queueing next match")
                        reset_runtime_for_next_match(
                            seeking_new_match_value=True,
                            post_game_waiting_empty_value=False,
                        )
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    # Aggressive action path: attempt leave/rematch frequently even if
                    # UI-state parsing misses transient countdown button states.
                    if now - last_post_game_action_attempt_at >= POST_GAME_ACTION_RETRY_GAP_SEC:
                        last_post_game_action_attempt_at = now
                        quick_acted = False
                        if args.auto_rematch:
                            quick_acted = click_rematch(page)
                            if quick_acted:
                                print("[bridge] Clicking rematch")
                                post_game_waiting_empty = True
                        else:
                            quick_acted = click_leave_room(page)
                            if quick_acted:
                                print("[bridge] Leaving room to find new match")
                                seeking_new_match = True

                        if quick_acted:
                            reset_runtime_for_next_match()
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                    def post_game_reload_fallback(reason: str) -> None:
                        print(f"[bridge] Post-game fallback: {reason}; reloading lobby")
                        try:
                            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
                            ensure_bridge_ready(page, config.board_selectors, site_mode)
                        except PlaywrightError as exc:
                            print(f"[bridge] Post-game fallback reload failed: {exc}")

                        # Reset match tracking and queue up new match flow.
                        reset_runtime_for_next_match(
                            seeking_new_match_value=True,
                            post_game_waiting_empty_value=False,
                        )

                    ui_state = read_post_game_ui_state(page)
                    if ui_state is None:
                        if args.post_game_reload_sec > 0 and post_game_elapsed >= args.post_game_reload_sec:
                            post_game_reload_fallback("controls not readable")
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    acted = False
                    if ui_state["opponent_left"] and ui_state["has_leave_room"]:
                        acted = click_leave_room(page)
                        if acted:
                            print("[bridge] Opponent left/disconnected; leaving room")
                            seeking_new_match = True
                    elif ui_state["has_rematch"] or ui_state["has_leave_room"]:
                        if args.auto_rematch and ui_state["has_rematch"] and not ui_state["opponent_left"]:
                            acted = click_rematch(page)
                            if acted:
                                print("[bridge] Clicking rematch")
                                post_game_waiting_empty = True
                        elif ui_state["has_leave_room"]:
                            acted = click_leave_room(page)
                            if acted:
                                print("[bridge] Leaving room to find new match")
                                seeking_new_match = True
                            elif now - last_lifecycle_log >= 2.0:
                                print("[bridge] Leave room detected but click failed; retrying")
                                last_lifecycle_log = now

                    if acted:
                        reset_runtime_for_next_match()
                    else:
                        if args.post_game_reload_sec > 0 and post_game_elapsed >= args.post_game_reload_sec:
                            post_game_reload_fallback("controls did not appear")
                        elif now - last_lifecycle_log >= 5.0:
                            print(
                                "[bridge] Waiting for leave room/rematch controls... "
                                f"({post_game_elapsed:.1f}s)"
                            )
                            last_lifecycle_log = now
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if args.mode == "auto" and site_mode == "papergames" and seeking_new_match:
                    if is_papergames_live_room_url(page.url):
                        # Match URL reached; leave queue-click flow and attach normally.
                        seeking_new_match = False
                    else:
                        now = time.time()
                        if now - last_queue_click_attempt_at >= QUEUE_CLICK_RETRY_GAP_SEC:
                            last_queue_click_attempt_at = now
                            if try_click_queue_controls(page):
                                print("[bridge] Queue click sent (play online/random player)")
                                queued_click_at = time.time()
                                # Exit queue-click loop and wait for match attach.
                                seeking_new_match = False
                        else:
                            if now - last_lifecycle_log >= 5.0:
                                print("[bridge] Waiting for 'Play online with a random player' button...")
                                last_lifecycle_log = now
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                if not match_active:
                    # Stay idle on the landing/lobby page until a match is entered.
                    # This prevents auto logic from trying to run before gameplay starts.
                    if not ensure_bridge_ready(page, config.board_selectors, site_mode):
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    probe_seq: Optional[str] = None
                    try:
                        # Probe sequence even while on lobby URL; some sites keep URL unchanged.
                        probe_seq, probe_source = probe_sequence(page)
                    except PlaywrightError:
                        probe_seq = None
                        probe_source = None

                    # After game-end cleanup, do not attach to a stale finished board.
                    # Wait until the board is truly empty once before re-attaching.
                    if site_mode == "papergames" and post_game_waiting_empty:
                        counts = read_grid_column_counts(page)
                        if counts is None or sum(counts) != 0:
                            now = time.time()
                            if now - last_post_game_wait_log >= 5.0:
                                print("[bridge] Waiting for fresh empty board before next game...")
                                last_post_game_wait_log = now
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                        post_game_waiting_empty = False
                        last_post_game_wait_log = 0.0
                        print("[bridge] Fresh board detected; ready for next game")

                    # Some papergames routes keep the same URL and may hide clear in-game
                    # text cues. If we can already read any board state (including empty ""),
                    # consider the match active.
                    board_signal_ready = probe_seq is not None

                    if site_mode == "papergames" and not is_papergames_live_room_url(page.url):
                        now = time.time()
                        # Some papergames transitions briefly keep non-/r/ URLs even
                        # though the in-game board is already active. Allow attach only
                        # when we have strong signals after a queue click.
                        can_attach_without_live_room = False
                        if args.mode == "auto" and queued_click_at is not None:
                            elapsed = now - queued_click_at
                            if elapsed >= QUEUE_ATTACH_SIGNAL_GRACE_SEC:
                                if has_in_game_ui(page):
                                    has_board_moves = isinstance(probe_seq, str) and len(probe_seq) > 0
                                    has_turn_banner = has_initial_your_turn_text(page)
                                    can_attach_without_live_room = has_board_moves or has_turn_banner
                                    if can_attach_without_live_room:
                                        print(
                                            "[bridge] In-game board detected on non-live URL; "
                                            "attaching without /en/r/..."
                                        )

                        if can_attach_without_live_room:
                            pass
                        elif (
                            args.mode == "auto"
                            and queued_click_at is not None
                            and (now - queued_click_at) >= QUEUE_RETRY_IDLE_SEC
                        ):
                            elapsed = now - queued_click_at
                            print(
                                "[bridge] Queue still not in live room URL "
                                f"after {elapsed:.1f}s; retrying queue"
                            )
                            seeking_new_match = True
                            queued_click_at = None
                            time.sleep(args.poll_ms / 1000.0)
                            continue
                        else:
                            if now - last_wait_log >= 5.0:
                                current_path = urlparse(page.url).path or "/"
                                print(
                                    "[bridge] Waiting for live match room URL (/en/r/...)... "
                                    f"(current={current_path})"
                                )
                                last_wait_log = now
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                    if in_lobby_url(page.url, args.url) and not has_in_game_ui(page) and not board_signal_ready:
                        now = time.time()
                        if (
                            args.mode == "auto"
                            and site_mode == "papergames"
                            and queued_click_at is not None
                            and (now - queued_click_at) >= QUEUE_RETRY_IDLE_SEC
                        ):
                            elapsed = now - queued_click_at
                            print(
                                "[bridge] Lobby still idle after queue click "
                                f"({elapsed:.1f}s); retrying queue"
                            )
                            seeking_new_match = True
                            queued_click_at = None
                            time.sleep(args.poll_ms / 1000.0)
                            continue
                        if now - last_wait_log >= 5.0:
                            print("[bridge] Waiting to enter a match...")
                            last_wait_log = now
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    match_active = True
                    queued_click_at = None
                    current_opponent = None
                    game_result_recorded = False
                    game_solve_total_sec = 0.0
                    game_solve_samples = 0
                    print(f"[bridge] Match detected at URL: {page.url}")
                    if probe_source == "storage" and probe_seq is not None:
                        initial_storage_sequence = probe_seq
                        print(f"[bridge] Captured initial storage sequence baseline: {initial_storage_sequence}")

                if match_active and current_opponent is None and site_mode == "papergames":
                    parsed_opp = read_opponent_username_strict(page, our_username=our_username)
                    if parsed_opp is not None:
                        current_opponent = parsed_opp
                        print(f"[bridge] Opponent detected: {current_opponent}")
                    else:
                        probe_fallback = debug_log_opponent_probe()
                        if probe_fallback is not None:
                            current_opponent = probe_fallback
                            print(f"[bridge] Opponent detected (fallback): {current_opponent}")

                if is_replay_page(page):
                    now = time.time()
                    if now - last_wait_log >= 5.0:
                        print("[bridge] Replay page detected (fast forward/back controls). Auto play is disabled until you enter a live match.")
                        last_wait_log = now
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if not ensure_bridge_ready(page, config.board_selectors, site_mode):
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if args.mode == "auto" and site_mode == "papergames" and not post_game_mode:
                    reason = detect_terminal_page_reason(page)
                    if reason is not None:
                        print(f"[bridge] Terminal page state detected: {reason}")
                        if not game_result_recorded:
                            mapped = result_from_terminal_reason(reason, detected_player)
                            if mapped is not None:
                                stats.record_game(
                                    mapped,
                                    detected_player,
                                    current_opponent,
                                    len(last_sequence or ""),
                                    game_solve_samples,
                                    game_solve_total_sec,
                                )
                                game_result_recorded = True
                                print(f"[bridge] Stats updated: {stats.summary_line()}")
                        if on_game_resolved_maybe_pause():
                            time.sleep(args.poll_ms / 1000.0)
                            continue
                        post_game_mode = True
                        post_game_started_at = time.time()
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                try:
                    seq, manual_sequence, seq_source = read_sequence(
                        page,
                        manual_fallback=args.manual_fallback,
                        game_url=args.url,
                        manual_mode=args.manual_input_mode,
                        manual_sequence=manual_sequence,
                        detected_player=detected_player,
                        initial_storage_sequence=initial_storage_sequence,
                    )
                except PlaywrightError:
                    # Execution context can reset during manual refresh; retry on next tick.
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                # Papergames-specific robust tracking: build sequence from per-column
                # count deltas so move order stays stable even if full-board
                # reconstruction is ambiguous.
                if site_mode == "papergames":
                    grid_counts = read_grid_column_counts(page)
                    if grid_counts is not None:
                        if sum(grid_counts) == 0:
                            empty_grid_streak += 1
                        else:
                            empty_grid_streak = 0

                        if last_grid_col_counts is None:
                            last_grid_col_counts = grid_counts
                            if seq_source == "grid" and isinstance(seq, str) and VALID_SEQ_RE.fullmatch(seq):
                                tracked_grid_sequence = seq
                            elif sum(grid_counts) == 0:
                                tracked_grid_sequence = ""
                        else:
                            deltas = [grid_counts[i] - last_grid_col_counts[i] for i in range(7)]
                            if any(d < 0 for d in deltas):
                                # Snapshot inconsistency can transiently report fewer tokens.
                                # Keep sticky tracking unless we are explicitly waiting for
                                # a fresh board between games.
                                if (
                                    post_game_waiting_empty
                                    and sum(grid_counts) == 0
                                    and empty_grid_streak >= EMPTY_RESET_STREAK
                                ):
                                    last_grid_col_counts = grid_counts
                                    tracked_grid_sequence = ""
                                elif (
                                    tracked_grid_sequence is None
                                    and isinstance(seq, str)
                                    and VALID_SEQ_RE.fullmatch(seq)
                                ):
                                    # Rebase when we have a coherent fresh grid parse but
                                    # previous baseline belongs to an older terminal board.
                                    last_grid_col_counts = grid_counts
                                    tracked_grid_sequence = seq
                                    debug_log_parse_snapshot(
                                        "grid_negative_delta_rebased_from_grid",
                                        {
                                            "last_grid_col_counts": last_grid_col_counts,
                                            "grid_counts": grid_counts,
                                            "tracked_grid_sequence": tracked_grid_sequence,
                                            "seq_source": seq_source,
                                        },
                                    )
                                else:
                                    # Ignore this unstable frame; keep prior tracker.
                                    debug_log_parse_snapshot(
                                        "grid_negative_delta_ignored",
                                        {
                                            "last_grid_col_counts": last_grid_col_counts,
                                            "grid_counts": grid_counts,
                                            "tracked_grid_sequence": tracked_grid_sequence,
                                            "empty_grid_streak": empty_grid_streak,
                                            "post_game_waiting_empty": post_game_waiting_empty,
                                        },
                                    )
                            else:
                                added = sum(deltas)
                                if added == 1 and deltas.count(1) == 1:
                                    col = deltas.index(1) + 1
                                    if tracked_grid_sequence is None:
                                        # Best-effort seed when attaching mid-game.
                                        tracked_grid_sequence = seq if isinstance(seq, str) else ""
                                    tracked_grid_sequence += str(col)
                                    last_grid_col_counts = grid_counts
                                elif added == 2:
                                    if tracked_grid_sequence is None:
                                        tracked_grid_sequence = seq if isinstance(seq, str) else ""

                                    plus_cols = []
                                    for i, d in enumerate(deltas):
                                        if d > 0:
                                            plus_cols.extend([i + 1] * d)

                                    appended = False
                                    # If one of the two moves matches the most recent suggestion,
                                    # consume that first (our move then opponent move).
                                    recent_col_plus_one: Optional[int] = None
                                    if last_suggested_col is not None:
                                        recent_col_plus_one = last_suggested_col + 1
                                    if (
                                        len(plus_cols) == 2
                                        and recent_col_plus_one is not None
                                        and (time.time() - last_suggested_at) <= 4.0
                                        and recent_col_plus_one in plus_cols
                                    ):
                                        first = recent_col_plus_one
                                        plus_cols.remove(first)
                                        second = plus_cols[0]
                                        tracked_grid_sequence += str(first) + str(second)
                                        appended = True

                                    # If both landed in same column, order is unambiguous.
                                    elif len(plus_cols) == 2 and plus_cols[0] == plus_cols[1]:
                                        tracked_grid_sequence += str(plus_cols[0]) + str(plus_cols[1])
                                        appended = True

                                    if appended:
                                        last_grid_col_counts = grid_counts
                                elif added == 0:
                                    pass
                                else:
                                    # Ignore unstable multi-cell jumps for one tick.
                                    debug_log_parse_snapshot(
                                        "grid_multi_cell_jump_ignored",
                                        {
                                            "deltas": deltas,
                                            "added": added,
                                            "last_grid_col_counts": last_grid_col_counts,
                                            "grid_counts": grid_counts,
                                            "tracked_grid_sequence": tracked_grid_sequence,
                                        },
                                    )

                        if tracked_grid_sequence is not None:
                            seq = tracked_grid_sequence
                            seq_source = "grid-delta"
                    elif tracked_grid_sequence is not None:
                        # Keep using stable tracked sequence if one poll misses grid counts.
                        seq = tracked_grid_sequence
                        seq_source = "grid-delta"

                    # Never downgrade from stable delta tracking back to ambiguous raw grid.
                    if tracked_grid_sequence is not None and seq_source == "grid":
                        seq = tracked_grid_sequence
                        seq_source = "grid-delta"

                if seq is None:
                    if args.mode == "auto" and site_mode == "papergames":
                        # Recovery path for aborted/disconnected matches where board
                        # sequence vanishes before normal terminal parsing catches up.
                        reason = detect_terminal_page_reason(page)
                        if reason is not None:
                            print(f"[bridge] Terminal/no-sequence state detected: {reason}")
                            if not game_result_recorded:
                                mapped = result_from_terminal_reason(reason, detected_player)
                                if mapped is not None:
                                    stats.record_game(
                                        mapped,
                                        detected_player,
                                        current_opponent,
                                        len(last_sequence or ""),
                                        game_solve_samples,
                                        game_solve_total_sec,
                                    )
                                    game_result_recorded = True
                                    print(f"[bridge] Stats updated: {stats.summary_line()}")
                            if on_game_resolved_maybe_pause():
                                time.sleep(args.poll_ms / 1000.0)
                                continue
                            post_game_mode = True
                            post_game_started_at = time.time()
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                        ui_state = read_post_game_ui_state(page)
                        if ui_state is not None and (
                            ui_state["opponent_left"]
                            or ui_state["has_leave_room"]
                            or ui_state["has_rematch"]
                        ):
                            print("[bridge] No sequence but post-game controls detected; entering post-game mode")
                            post_game_mode = True
                            post_game_started_at = time.time()
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                        if in_lobby_url(page.url, args.url) and not has_in_game_ui(page):
                            print("[bridge] No sequence and lobby detected; recovering to queue flow")
                            reset_runtime_for_next_match(seeking_new_match_value=True)
                            queued_click_at = None
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                    now = time.time()
                    if now - last_wait_log >= 5.0:
                        print("[bridge] Waiting for detectable sequence...")
                        last_wait_log = now
                    debug_log_parse_snapshot(
                        "sequence_missing",
                        {
                            "last_sequence": last_sequence,
                            "tracked_grid_sequence": tracked_grid_sequence,
                            "detected_player": detected_player,
                            "post_game_mode": post_game_mode,
                        },
                        min_interval_sec=5.0,
                    )
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if (
                    site_mode == "papergames"
                    and last_sequence is not None
                    and seq != last_sequence
                    and seq_source in {"grid", "grid-delta"}
                ):
                    if seq.startswith(last_sequence):
                        # Allow at most two new moves per tick in papergames flow.
                        # Larger jumps are usually unstable reconstruction frames.
                        if len(seq) > len(last_sequence) + 2:
                            now = time.time()
                            if now - last_non_monotonic_log >= 5.0:
                                print("[bridge] Ignoring unstable multi-move papergames snapshot")
                                last_non_monotonic_log = now
                            debug_log_parse_snapshot(
                                "papergames_multi_move_snapshot_ignored",
                                {
                                    "last_sequence": last_sequence,
                                    "candidate_sequence": seq,
                                    "seq_source": seq_source,
                                },
                                min_interval_sec=5.0,
                            )
                            time.sleep(args.poll_ms / 1000.0)
                            continue
                    else:
                        inferred = infer_single_move_from_count_delta(last_sequence, seq)
                        if inferred is not None:
                            seq = last_sequence + inferred
                            seq_source = "grid-delta-recovered"
                            now = time.time()
                            if now - last_non_monotonic_log >= 5.0:
                                print(
                                    "[bridge] Recovered papergames non-monotonic snapshot "
                                    f"via count delta: +{inferred}"
                                )
                                last_non_monotonic_log = now
                        else:
                            now = time.time()
                            if now - last_non_monotonic_log >= 5.0:
                                print("[bridge] Ignoring non-monotonic papergames snapshot")
                                last_non_monotonic_log = now
                            debug_log_parse_snapshot(
                                "papergames_non_monotonic_snapshot_ignored",
                                {
                                    "last_sequence": last_sequence,
                                    "candidate_sequence": seq,
                                    "seq_source": seq_source,
                                },
                                min_interval_sec=5.0,
                            )
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                # Stabilize grid parsing: require a changed sequence to be seen twice
                # before acting on it, which filters transient highlight/animation frames.
                if seq_source == "grid" and site_mode != "papergames" and seq != last_sequence:
                    if seq == grid_seq_candidate:
                        grid_seq_candidate_count += 1
                    else:
                        grid_seq_candidate = seq
                        grid_seq_candidate_count = 1
                    if grid_seq_candidate_count < 2:
                        time.sleep(args.poll_ms / 1000.0)
                        continue
                    grid_seq_candidate = None
                    grid_seq_candidate_count = 0
                elif seq == last_sequence:
                    grid_seq_candidate = None
                    grid_seq_candidate_count = 0
                    inferred_move_candidate = None
                    inferred_move_candidate_count = 0

                if (
                    seq_source == "grid"
                    and site_mode != "papergames"
                    and last_sequence is not None
                    and seq.startswith(last_sequence)
                    and len(seq) > len(last_sequence) + 1
                ):
                    now = time.time()
                    if now - last_non_monotonic_log >= 5.0:
                        print("[bridge] Ignoring multi-move grid jump; waiting for single committed move")
                        last_non_monotonic_log = now
                    debug_log_parse_snapshot(
                        "grid_multi_move_jump_ignored",
                        {
                            "last_sequence": last_sequence,
                            "candidate_sequence": seq,
                            "seq_source": seq_source,
                        },
                        min_interval_sec=5.0,
                    )
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                # Keep grid history monotonic: accept only extensions of the last
                # confirmed sequence. This avoids ambiguous board reconstructions
                # from rewriting earlier moves.
                if (
                    seq_source == "grid"
                    and site_mode != "papergames"
                    and last_sequence is not None
                    and seq != last_sequence
                    and not seq.startswith(last_sequence)
                ):
                    inferred = infer_single_move_from_count_delta(last_sequence, seq)
                    if inferred is not None:
                        if inferred == inferred_move_candidate:
                            inferred_move_candidate_count += 1
                        else:
                            inferred_move_candidate = inferred
                            inferred_move_candidate_count = 1

                        if inferred_move_candidate_count < 2:
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                        seq = last_sequence + inferred
                        inferred_move_candidate = None
                        inferred_move_candidate_count = 0
                    else:
                        now = time.time()
                        if now - last_non_monotonic_log >= 5.0:
                            print(
                                "[bridge] Ignoring non-monotonic grid sequence; waiting for stable extension"
                            )
                            last_non_monotonic_log = now
                        debug_log_parse_snapshot(
                            "grid_non_monotonic_sequence_ignored",
                            {
                                "last_sequence": last_sequence,
                                "candidate_sequence": seq,
                                "seq_source": seq_source,
                            },
                            min_interval_sec=5.0,
                        )
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    now = time.time()
                    if now - last_non_monotonic_log >= 5.0:
                        print(
                            f"[bridge] Recovered non-monotonic grid sequence via count delta: +{inferred}"
                        )
                        last_non_monotonic_log = now

                if (
                    site_mode == "papergames"
                    and last_sequence is not None
                    and seq != last_sequence
                    and has_same_column_counts(seq, last_sequence)
                ):
                    now = time.time()
                    if now - last_equivalent_log >= 5.0:
                        print("[bridge] Equivalent papergames snapshot reorder detected; preserving stable sequence")
                        last_equivalent_log = now
                    seq = last_sequence
                    seq_source = "grid-delta-equiv"

                if seq != last_sequence:
                    prev_sequence = last_sequence
                    if seq_source:
                        print(f"[bridge] sequence={seq} (source={seq_source})")
                    else:
                        print(f"[bridge] sequence={seq}")

                    if (
                        prev_sequence is not None
                        and len(seq) == len(prev_sequence) + 1
                        and detected_player is not None
                        and not is_our_turn(prev_sequence, detected_player)
                    ):
                        opp_col = seq[-1]
                        print(f"[bridge] Opponent move: column {opp_col}")

                    last_sequence = seq

                try:
                    seq_status = solver.status(seq)
                except RuntimeError as exc:
                    print(f"[bridge] Solver status error: {exc}")
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if seq_status == "invalid":
                    now = time.time()
                    if now - last_terminal_log >= 3.0:
                        print("[bridge] Ignoring invalid sequence snapshot")
                        last_terminal_log = now
                    debug_log_parse_snapshot(
                        "solver_invalid_sequence_snapshot",
                        {
                            "sequence": seq,
                            "seq_source": seq_source,
                            "last_sequence": last_sequence,
                        },
                        min_interval_sec=3.0,
                    )

                    if site_mode == "papergames":
                        # Rebase delta trackers to recover from occasional stale/garbled frames.
                        counts = read_grid_column_counts(page)
                        if counts is not None:
                            last_grid_col_counts = counts
                            tracked_grid_sequence = "" if sum(counts) == 0 else None
                            empty_grid_streak = 1 if sum(counts) == 0 else 0

                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if seq_status in {"win1", "win2", "draw"}:
                    if seq_status == "draw":
                        outcome = "Draw"
                    else:
                        winner_side = 1 if seq_status == "win1" else 2
                        winner_name: Optional[str] = None
                        if detected_player in {1, 2}:
                            if winner_side == detected_player:
                                winner_name = our_username or "You"
                            else:
                                winner_name = current_opponent
                        if not winner_name:
                            winner_name = f"Player {winner_side}"
                        outcome = f"{winner_name} win"
                    print(f"[bridge] Game finished: {outcome}")

                    if not game_result_recorded:
                        mapped = result_from_seq_status(seq_status, detected_player)
                        if mapped is not None:
                            stats.record_game(
                                mapped,
                                detected_player,
                                current_opponent,
                                len(seq),
                                game_solve_samples,
                                game_solve_total_sec,
                            )
                            game_result_recorded = True
                            print(f"[bridge] Stats updated: {stats.summary_line()}")

                    if on_game_resolved_maybe_pause():
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    if args.mode == "auto" and site_mode == "papergames":
                        post_game_mode = True
                        post_game_started_at = time.time()
                    else:
                        print("[bridge] Resetting state for next game")
                        reset_runtime_for_next_match(post_game_waiting_empty_value=True)
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                # Board changed, clear any temporary suppression for prior failed sequence.
                if blocked_sequence is not None:
                    same_blocked = seq == blocked_sequence
                    if (
                        not same_blocked
                        and site_mode == "papergames"
                        and has_same_column_counts(seq, blocked_sequence)
                    ):
                        same_blocked = True
                    if not same_blocked:
                        blocked_sequence = None
                        blocked_sequence_until = 0.0

                # After an auto-click, wait until board state advances before making a new decision.
                if pending_expected_sequence is not None:
                    if args.mode == "auto" and site_mode == "papergames":
                        reason = detect_terminal_page_reason(page)
                        if reason is not None:
                            if handle_terminal_transition(
                                reason,
                                "Terminal state detected while awaiting pending move",
                                sequence_len_hint=len(seq),
                            ):
                                time.sleep(args.poll_ms / 1000.0)
                                continue

                    expected_confirmed = seq == pending_expected_sequence
                    if (
                        not expected_confirmed
                        and site_mode == "papergames"
                        and has_same_column_counts(seq, pending_expected_sequence)
                    ):
                        expected_confirmed = True

                    base_unchanged = pending_base_sequence is not None and seq == pending_base_sequence
                    if (
                        pending_base_sequence is not None
                        and not base_unchanged
                        and site_mode == "papergames"
                        and has_same_column_counts(seq, pending_base_sequence)
                    ):
                        base_unchanged = True

                    if expected_confirmed:
                        pending_expected_sequence = None
                        pending_base_sequence = None
                        pending_col = None
                        pending_retry_attempted = False
                        pending_move_started_at = None
                    elif pending_base_sequence is not None and not base_unchanged:
                        # Board progressed differently than expected; stop waiting and re-evaluate.
                        print("[bridge] Pending move diverged from expected board state; re-evaluating")
                        pending_expected_sequence = None
                        pending_base_sequence = None
                        pending_col = None
                        pending_retry_attempted = False
                        pending_move_started_at = None
                    else:
                        now = time.time()
                        if pending_move_started_at is None:
                            pending_move_started_at = now

                        elapsed = now - pending_move_started_at
                        if elapsed > AUTO_COMMIT_TIMEOUT_SEC:
                            if args.mode == "auto" and pending_col is not None and not pending_retry_attempted:
                                try:
                                    retry_col = pending_col
                                    if not isinstance(retry_col, int):
                                        time.sleep(args.poll_ms / 1000.0)
                                        continue
                                    method = play_column(page, retry_col, site_mode)
                                    if method is not None:
                                        print(f"[bridge] Retrying pending move: column {retry_col + 1} ({method})")
                                        pending_retry_attempted = True
                                        pending_move_started_at = time.time()
                                except PlaywrightError:
                                    pass
                        if elapsed > PENDING_MAX_WAIT_SEC:
                            base_seq = pending_base_sequence if pending_base_sequence is not None else seq
                            print("[bridge] Pending move not confirmed; releasing lock and re-evaluating")
                            pending_expected_sequence = None
                            pending_base_sequence = None
                            pending_col = None
                            pending_retry_attempted = False
                            pending_move_started_at = None
                            blocked_sequence = base_seq
                            blocked_sequence_until = now + FAILED_SEQUENCE_COOLDOWN_SEC
                        else:
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                        time.sleep(args.poll_ms / 1000.0)
                        continue

                if detected_player is None:
                    if fixed_player is None and site_mode == "papergames":
                        if len(seq) == 0:
                            if has_initial_your_turn_text(page):
                                detected_player = 1
                                auto_side_probe_started_at = None
                            else:
                                now = time.time()
                                if auto_side_probe_started_at is None:
                                    auto_side_probe_started_at = now
                                if now - auto_side_probe_started_at >= 1.5:
                                    detected_player = 2
                                    auto_side_probe_started_at = None
                                else:
                                    time.sleep(args.poll_ms / 1000.0)
                                    continue
                        elif len(seq) == 1:
                            # Opponent played first before banner was observed.
                            detected_player = 2
                            auto_side_probe_started_at = None
                        else:
                            # Mid-game attach fallback without user prompt.
                            detected_player = 1 if len(seq) % 2 == 0 else 2
                            auto_side_probe_started_at = None
                    else:
                        detected_player = detect_player_from_sequence(seq)

                    print(f"[bridge] Using player side: {detected_player}")

                if args.block_ads and not blocking_active:
                    # Activate blocking only once gameplay is detected.
                    # This avoids interfering with lobby/matchmaking flow.
                    install_request_blocking(page, first_party_host, args.block_level)
                    blocking_active = True
                    print(f"[bridge] Ad/tracker blocking enabled ({args.block_level})")

                if not is_our_turn(seq, detected_player):
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if blocked_sequence is not None:
                    same_blocked = seq == blocked_sequence
                    if (
                        not same_blocked
                        and site_mode == "papergames"
                        and has_same_column_counts(seq, blocked_sequence)
                    ):
                        same_blocked = True
                    if same_blocked:
                        now = time.time()
                        if now < blocked_sequence_until:
                            if now - last_block_log >= 5.0:
                                print("[bridge] Waiting for board change before retrying same move")
                                last_block_log = now
                            time.sleep(args.poll_ms / 1000.0)
                            continue
                        blocked_sequence = None
                        blocked_sequence_until = 0.0

                solved_cache_hit = last_solved_sequence == seq and last_solved_col is not None
                if (
                    not solved_cache_hit
                    and site_mode == "papergames"
                    and last_solved_sequence is not None
                    and last_solved_col is not None
                    and has_same_column_counts(last_solved_sequence, seq)
                ):
                    solved_cache_hit = True

                move_col_raw: Optional[int] = None
                if solved_cache_hit:
                    if last_solved_col is None:
                        # Defensive guard for static type narrowing and stale cache edge cases.
                        time.sleep(args.poll_ms / 1000.0)
                        continue
                    move_col_raw = last_solved_col
                else:
                    try:
                        solve_started_at = time.time()
                        move_col_raw = solver.best_move(seq)
                        solve_elapsed = time.time() - solve_started_at
                    except RuntimeError as exc:
                        print(f"[bridge] Solver error: {exc}")
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    game_solve_total_sec += solve_elapsed
                    game_solve_samples += 1

                    if solve_elapsed > SLOW_SOLVE_THRESHOLD_SEC and seq not in slow_logged_sequences:
                        record_slow_solve(seq, solve_elapsed, args.weak)
                        slow_logged_sequences.add(seq)
                        print(
                            f"[bridge] Slow solve recorded (> {SLOW_SOLVE_THRESHOLD_SEC:.0f}s): "
                            f"seq={seq} took {solve_elapsed:.2f}s"
                        )

                    # If solver took a while, verify sequence hasn't advanced before using result.
                    if solve_elapsed > 0.15:
                        try:
                            latest_seq, _, _ = read_sequence(
                                page,
                                manual_fallback=False,
                                game_url=args.url,
                                manual_mode=args.manual_input_mode,
                                manual_sequence=manual_sequence,
                                detected_player=detected_player,
                                initial_storage_sequence=initial_storage_sequence,
                            )
                        except PlaywrightError:
                            latest_seq = None

                        if isinstance(latest_seq, str) and latest_seq != seq:
                            if site_mode == "papergames":
                                # Raw papergames snapshots can reorder history while still
                                # representing the same board. Treat equivalent column counts
                                # as unchanged, and only discard if board actually progressed.
                                if has_same_column_counts(latest_seq, seq):
                                    latest_seq = seq
                                else:
                                    progressed = infer_single_move_from_count_delta(seq, latest_seq) is not None
                                    if not progressed and len(latest_seq) < len(seq):
                                        # Regressive/unstable frame; keep current solved result.
                                        latest_seq = seq

                        if isinstance(latest_seq, str) and latest_seq != seq:
                            print(
                                "[bridge] Discarding solved move: board changed during solve "
                                f"({seq} -> {latest_seq})"
                            )
                            debug_log_parse_snapshot(
                                "discard_solved_move_board_changed",
                                {
                                    "solved_sequence": seq,
                                    "latest_sequence": latest_seq,
                                    "solve_elapsed_sec": round(solve_elapsed, 4),
                                },
                                min_interval_sec=2.0,
                            )
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                    if move_col_raw is None:
                        time.sleep(args.poll_ms / 1000.0)
                        continue
                    last_solved_sequence = seq
                    last_solved_col = int(move_col_raw)

                    if args.mode == "auto" and site_mode == "papergames":
                        reason = detect_terminal_page_reason(page)
                        if reason is not None:
                            if handle_terminal_transition(
                                reason,
                                "Terminal state detected after solve",
                                sequence_len_hint=len(seq),
                            ):
                                time.sleep(args.poll_ms / 1000.0)
                                continue

                if move_col_raw is None:
                    time.sleep(args.poll_ms / 1000.0)
                    continue
                if not isinstance(move_col_raw, int):
                    time.sleep(args.poll_ms / 1000.0)
                    continue
                move_col = move_col_raw

                if last_logged_suggestion_seq != seq or last_logged_suggestion_col != move_col:
                    print(f"[bridge] Suggested move: column {move_col + 1}")
                    last_logged_suggestion_seq = seq
                    last_logged_suggestion_col = move_col
                last_suggested_col = move_col
                last_suggested_at = time.time()

                if args.mode == "observe":
                    time.sleep(args.poll_ms / 1000.0)
                    continue

                if args.mode == "assist":
                    ans = input("[bridge] Press Enter to play suggested move, or 's' to skip: ").strip().lower()
                    if ans == "s":
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                try:
                    method = play_column(page, move_col, site_mode)
                    if method is not None:
                        print(f"[bridge] Played column {move_col + 1} ({method})")
                        pending_expected_sequence = seq + str(move_col + 1)
                        pending_base_sequence = seq
                        pending_col = move_col
                        pending_retry_attempted = False
                        pending_move_started_at = time.time()
                        if args.manual_fallback and args.manual_input_mode == "incremental":
                            manual_sequence = seq + str(move_col + 1)
                            last_sequence = manual_sequence
                            print(f"[bridge] sequence={manual_sequence}")
                except PlaywrightError:
                    print("[bridge] Click failed during navigation/refresh; retrying")
                time.sleep(args.poll_ms / 1000.0)

            context.close()
            if browser is not None:
                browser.close()

    except KeyboardInterrupt:
        print("\n[bridge] Stopped by user")
        print(f"[bridge] Final stats: {stats.summary_line()}")
    except PlaywrightTimeoutError as exc:
        print(f"[bridge] Browser timeout: {exc}")
        return 1
    except PlaywrightError as exc:
        print(f"[bridge] Browser error: {exc}")
        return 1
    finally:
        operator_console_stop.set()
        solver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
