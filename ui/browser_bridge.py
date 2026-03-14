#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

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
# If lobby remains idle this long after a queue click, retry queueing.
QUEUE_RETRY_IDLE_SEC = 8.0
# Minimum gap between queue click attempts.
QUEUE_CLICK_RETRY_GAP_SEC = 1.5
# Minimum gap between post-game leave/rematch click attempts.
POST_GAME_ACTION_RETRY_GAP_SEC = 0.6
# Delay before attempting post-game leave/rematch actions.
POST_GAME_ACTION_DELAY_SEC = 5.0


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


def is_our_turn(move_sequence: str, player: int) -> bool:
    if player == 1:
        return len(move_sequence) % 2 == 0
    return len(move_sequence) % 2 == 1


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


def normalize_url_for_compare(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def in_lobby_url(current_url: str, game_url: str) -> bool:
    return normalize_url_for_compare(current_url) == normalize_url_for_compare(game_url)


def read_grid_column_counts(page) -> Optional[list[int]]:
    try:
        raw = page.evaluate("() => window.__c4Bridge.readGridColumnCounts()")
    except PlaywrightError:
        return None

    if not isinstance(raw, list) or len(raw) != 7:
        return None

    if not all(isinstance(x, int) and 0 <= x <= 6 for x in raw):
        return None

    return raw


def infer_single_move_from_count_delta(prev_seq: str, candidate_seq: str) -> Optional[str]:
    """Infer one newly added move from per-column count deltas.

    Useful when board reconstruction returns a non-prefix sequence that still
    represents the same board plus one committed move.
    """
    if not VALID_SEQ_RE.fullmatch(prev_seq) or not VALID_SEQ_RE.fullmatch(candidate_seq):
        return None

    prev_counts = [0] * 7
    cand_counts = [0] * 7
    for ch in prev_seq:
        prev_counts[ord(ch) - ord("1")] += 1
    for ch in candidate_seq:
        cand_counts[ord(ch) - ord("1")] += 1

    deltas = [cand_counts[i] - prev_counts[i] for i in range(7)]
    if any(d < 0 for d in deltas):
        return None

    total_added = sum(deltas)
    if total_added != 1:
        return None

    added_col = deltas.index(1)
    return str(added_col + 1)


def has_same_column_counts(seq_a: str, seq_b: str) -> bool:
    if not VALID_SEQ_RE.fullmatch(seq_a) or not VALID_SEQ_RE.fullmatch(seq_b):
        return False
    if len(seq_a) != len(seq_b):
        return False

    counts_a = [0] * 7
    counts_b = [0] * 7
    for ch in seq_a:
        counts_a[ord(ch) - ord("1")] += 1
    for ch in seq_b:
        counts_b[ord(ch) - ord("1")] += 1
    return counts_a == counts_b


def read_sequence(
    page,
    manual_fallback: bool,
    game_url: str,
    manual_mode: str,
    manual_sequence: Optional[str],
    detected_player: Optional[int],
    initial_storage_sequence: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    detected = page.evaluate("() => window.__c4Bridge.readMoveSequenceDetailed()")
    seq = None
    source = None
    if isinstance(detected, dict):
        seq = detected.get("sequence")
        source = detected.get("source")
    elif isinstance(detected, str):
        seq = detected

    if isinstance(seq, str) and VALID_SEQ_RE.fullmatch(seq):
        # Ignore stale storage-derived initial value captured at match entry.
        if source == "storage" and initial_storage_sequence is not None and seq == initial_storage_sequence:
            return None, manual_sequence, None
        return seq, seq, source

    if not manual_fallback:
        return None, manual_sequence, None

    # Do not prompt while the user is still on the setup/lobby URL.
    # This avoids blocking before the match actually begins.
    current_url = normalize_url_for_compare(page.url)
    target_url = normalize_url_for_compare(game_url)
    if current_url == target_url:
        return None, manual_sequence, None

    if manual_mode == "full":
        raw = input("[bridge] Enter current move sequence (digits 1-7), or blank to skip: ").strip()
        if not raw:
            return None, manual_sequence, None
        if not VALID_SEQ_RE.fullmatch(raw):
            print("[bridge] Invalid sequence format; expected digits 1-7 only")
            return None, manual_sequence, None
        print("[bridge] sequence source=manual-full")
        return raw, raw, "manual"

    # Incremental mode: keep sequence state and only ask for opponent move.
    if manual_sequence is None:
        seed = input(
            "[bridge] Enter known sequence to seed tracking (or blank to skip): "
        ).strip()
        if not seed:
            return None, manual_sequence, None
        if not VALID_SEQ_RE.fullmatch(seed):
            print("[bridge] Invalid sequence format; expected digits 1-7 only")
            return None, manual_sequence, None
        print("[bridge] sequence source=manual-seed")
        return seed, seed, "manual"

    if detected_player is not None and is_our_turn(manual_sequence, detected_player):
        return manual_sequence, manual_sequence, "manual"

    raw = input(
        "[bridge] Opponent move [1-7], blank=skip, u=undo, r=reset, =SEQ to replace: "
    ).strip()

    if not raw:
        return None, manual_sequence, None
    if raw == "u":
        if manual_sequence:
            manual_sequence = manual_sequence[:-1]
            print(f"[bridge] sequence={manual_sequence}")
        return None, manual_sequence, None
    if raw == "r":
        print("[bridge] sequence reset")
        return "", "", "manual"
    if raw.startswith("="):
        replacement = raw[1:]
        if not VALID_SEQ_RE.fullmatch(replacement):
            print("[bridge] Invalid replacement sequence; expected digits 1-7 only")
            return None, manual_sequence, None
        return replacement, replacement, "manual"
    if len(raw) == 1 and raw in "1234567":
        updated = manual_sequence + raw
        print("[bridge] sequence source=manual-incremental")
        return updated, updated, "manual"

    print("[bridge] Invalid input; use 1-7, blank, u, r, or =SEQ")
    return None, manual_sequence, None


def click_column(page, col: int) -> bool:
    rect = page.evaluate("() => window.__c4Bridge.boardRect()")
    if not isinstance(rect, dict):
        print("[bridge] Could not detect board rectangle for click")
        return False

    width = float(rect["width"])
    x = float(rect["x"]) + (col + 0.5) * (width / 7.0)
    y = float(rect["y"]) + 10.0
    page.mouse.click(x, y)
    return True


def click_column_dom(page, col: int) -> bool:
    try:
        ok = page.evaluate("(c) => window.__c4Bridge.clickColumnDom(c)", col)
    except PlaywrightError:
        return False
    return bool(ok)


def play_column(page, col: int, site_mode: str) -> Optional[str]:
    # Prefer DOM-targeted clicks in papergames mode, then fallback to coordinate click.
    if site_mode == "papergames" and click_column_dom(page, col):
        return "dom"
    if click_column(page, col):
        return "coord"
    return None


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


def probe_sequence(page) -> tuple[Optional[str], Optional[str]]:
    """Return (sequence, source) where source is one of: cells, storage, or None."""
    try:
        detected = page.evaluate("() => window.__c4Bridge.readMoveSequenceDetailed()")
    except PlaywrightError:
        return None, None

    if isinstance(detected, dict):
        seq = detected.get("sequence")
        source = detected.get("source")
        if isinstance(seq, str) and VALID_SEQ_RE.fullmatch(seq):
            return seq, source if isinstance(source, str) else None
        return None, None

    if isinstance(detected, str) and VALID_SEQ_RE.fullmatch(detected):
        return detected, None

    return None, None


def is_replay_page(page) -> bool:
    try:
        return bool(page.evaluate("""
            () => {
              const hasFastBack = !!document.querySelector('[aria-label="Fast backward"]');
              const hasFastForward = !!document.querySelector('[aria-label="Fast forward"]');
              return hasFastBack && hasFastForward;
            }
        """))
    except PlaywrightError:
        return False


def has_in_game_ui(page) -> bool:
    """Detect whether the page appears to be an active game UI rather than landing screen."""
    try:
        return bool(page.evaluate("""
            () => {
              const bodyText = (document.body?.innerText || '').toLowerCase();
              const hasResignText = bodyText.includes('resign');
              const hasLeaveRoom = !!document.querySelector('[aria-label="Leave room"]');
              const hasPlaybackOnly = !!document.querySelector('[aria-label="Fast backward"]') &&
                                      !!document.querySelector('[aria-label="Fast forward"]');
              // Playback pages are not active game UI for autoplay.
              if (hasPlaybackOnly) return false;
                            return hasResignText || hasLeaveRoom;
            }
        """))
    except PlaywrightError:
        return False


def has_initial_your_turn_text(page) -> bool:
    """Detect initial papergames turn banner: IT'S YOUR TURN."""
    try:
        return bool(page.evaluate("""
            () => {
              const text = (document.body?.innerText || '').toLowerCase();
              const normalized = text.replace(/[’']/g, "'");
              return normalized.includes("it's your turn") || normalized.includes("its your turn");
            }
        """))
    except PlaywrightError:
        return False


def detect_terminal_page_reason(page) -> Optional[str]:
    try:
        reason = page.evaluate("""
            () => {
              const text = (document.body?.innerText || '').toLowerCase();
              const checks = [
                ['you won', 'you won'],
                ['you lost', 'you lost'],
                ['draw', 'draw'],
                ['timed out', 'timed out'],
                ['timeout', 'timeout'],
                ['disconnected', 'disconnected'],
                ['opponent left', 'opponent left'],
                ['opponent disconnected', 'opponent disconnected'],
                                ['opponent aborted', 'opponent aborted'],
                                ['aborted', 'aborted'],
                                ['cancelled', 'cancelled'],
                                ['canceled', 'canceled'],
                                ['surrendered', 'surrendered'],
                ['game over', 'game over'],
              ];
              for (const [needle, tag] of checks) {
                if (text.includes(needle)) return tag;
              }
              return null;
            }
        """)
    except PlaywrightError:
        return None

    return reason if isinstance(reason, str) else None


def read_post_game_ui_state(page) -> Optional[dict]:
    try:
        raw = page.evaluate("""
            () => {
              const text = (document.body?.innerText || '').toLowerCase();
              const clickable = Array.from(document.querySelectorAll('button, [role="button"], a, .btn, [class*="button"]'));
              const labels = clickable.map((el) => (el.innerText || el.textContent || '').toLowerCase()).join('\n');
              return {
                hasRematch: labels.includes('rematch'),
                hasLeaveRoom: labels.includes('leave room') || !!document.querySelector('[aria-label="Leave room"]'),
                opponentLeft: text.includes('opponent left') || text.includes('opponent disconnected') || text.includes('disconnected'),
              };
            }
        """)
    except PlaywrightError:
        return None

    if not isinstance(raw, dict):
        return None
    return {
        "has_rematch": bool(raw.get("hasRematch")),
        "has_leave_room": bool(raw.get("hasLeaveRoom")),
        "opponent_left": bool(raw.get("opponentLeft")),
    }


def click_button_by_text_tokens(page, tokens: list[str]) -> bool:
    safe_tokens = [t.lower() for t in tokens if t]
    if not safe_tokens:
        return False
    try:
        ok = page.evaluate(
            """
            (tokens) => {
              const isVisible = (el) => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
              };
              const clickables = Array.from(document.querySelectorAll('button, [role="button"], a, .btn, [class*="button"], [aria-label]'));
              for (const el of clickables) {
                const txt = ((el.innerText || el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
                if (!tokens.every((t) => txt.includes(t))) continue;
                if (!isVisible(el)) continue;
                try {
                  el.click();
                  return true;
                } catch (_) {}
              }
              return false;
            }
            """,
            safe_tokens,
        )
    except PlaywrightError:
        return False
    return bool(ok)


def click_leave_room(page) -> bool:
        # Fast path for explicit ARIA target.
        try:
                page.click('[aria-label="Leave room"]', timeout=400)
                return True
        except Exception:
                pass

        # Papergames often renders this as a styled .btn span with nested countdown text.
        # Prefer native locator clicks, then force/coordinate fallbacks.
        leave_locators = [
                "button:has-text('Leave room')",
                "[role='button']:has-text('Leave room')",
                "a:has-text('Leave room')",
                ".btn:has-text('Leave room')",
                ".front.text.btn.btn-light:has-text('Leave room')",
                "span.btn.btn-light:has-text('Leave room')",
                "span.front.text.btn.btn-light:has-text('Leave room')",
                ".juicy-btn-inner:has-text('Leave room')",
                "span:has-text('Leave room')",
        ]
        for sel in leave_locators:
                try:
                        page.locator(sel).first.click(timeout=700)
                        return True
                except Exception:
                        try:
                                page.locator(sel).first.click(timeout=700, force=True)
                                return True
                        except Exception:
                                try:
                                        box = page.locator(sel).first.bounding_box()
                                        if box is not None and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                                                cx = box["x"] + box["width"] / 2.0
                                                cy = box["y"] + box["height"] / 2.0
                                                page.mouse.click(cx, cy)
                                                return True
                                except Exception:
                                        pass

        # Fallback: find text node container and click nearest likely clickable ancestor.
        try:
                ok = page.evaluate(
                        r"""
                        () => {
                            const isVisible = (el) => {
                                const r = el.getBoundingClientRect();
                                return r.width > 0 && r.height > 0;
                            };
                            const canClick = (el) => {
                                if (!el || !isVisible(el)) return false;
                                const disabled = (el.getAttribute('disabled') !== null) ||
                                                                 (el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                                return !disabled;
                            };
                            const nodes = Array.from(document.querySelectorAll('button, [role="button"], a, .btn, .juicy-btn-inner, span'));
                            for (const el of nodes) {
                                const txt = (el.textContent || '').toLowerCase().replace(/\s+/g, ' ').trim();
                                if (!txt.includes('leave room')) continue;

                                let cur = el;
                                for (let i = 0; i < 5 && cur; i++) {
                                    if (canClick(cur)) {
                                        try {
                                            cur.click();
                                            return true;
                                        } catch (_) {}
                                    }
                                    cur = cur.parentElement;
                                }
                            }
                            return false;
                        }
                        """
                )
                if bool(ok):
                        return True
        except PlaywrightError:
                pass

        return click_button_by_text_tokens(page, ["leave room"])


def click_rematch(page) -> bool:
    return click_button_by_text_tokens(page, ["rematch"])


def click_play_online_random(page) -> bool:
    # Prefer a direct native click on papergames' juicy card CTA first.
    # This avoids cases where synthetic DOM .click() on inner spans is ignored.
    try:
        page.locator(
            ".juicy-btn-inner:has-text('Play online'):has-text('random player')"
        ).first.click(timeout=600)
        return True
    except Exception:
        pass
    return click_button_by_text_tokens(page, ["play online", "random player"])


def click_play_online(page) -> bool:
    return click_button_by_text_tokens(page, ["play online"])


def click_random_player(page) -> bool:
    return click_button_by_text_tokens(page, ["random player"])


def try_click_queue_controls(page) -> bool:
    """Try common papergames queue flows.

    Some flows expose one combined CTA, while others require clicking
    "Play online" then selecting "Random player".
    """
    if click_play_online_random(page):
        return True
    if click_random_player(page):
        return True
    if click_play_online(page):
        # Best-effort second step if a picker appears after the first click.
        time.sleep(0.15)
        if click_random_player(page):
            return True
        # Do not report success yet; keep retrying until random-player queue is selected.
        return False
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

    print("[bridge] Starting browser bridge")
    print(f"[bridge] URL: {args.url}")
    print(f"[bridge] Browser: {args.browser}")
    print(f"[bridge] Site mode: {site_mode}")
    print(f"[bridge] Mode: {args.mode}, player={args.player}, weak={args.weak}")
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

            while True:
                if page.is_closed():
                    print("[bridge] Page was closed; stopping bridge")
                    return 0

                if args.mode == "auto" and site_mode == "papergames" and post_game_mode:
                    now = time.time()
                    if post_game_started_at is None:
                        post_game_started_at = now
                    post_game_elapsed = now - post_game_started_at

                    if post_game_elapsed < args.post_game_wait_sec:
                        if now - last_lifecycle_log >= 2.0:
                            print(
                                "[bridge] Terminal wait before post-game action... "
                                f"({post_game_elapsed:.1f}/{args.post_game_wait_sec:.1f}s)"
                            )
                            last_lifecycle_log = now
                        time.sleep(args.poll_ms / 1000.0)
                        continue

                    # Papergames may auto-redirect to lobby after a short delay.
                    # If that happens, stop waiting for in-game post-game controls
                    # and switch directly to the matchmaking flow.
                    if in_lobby_url(page.url, args.url) and not has_in_game_ui(page):
                        print("[bridge] Post-game redirect to lobby detected; queueing next match")
                        match_active = False
                        post_game_mode = False
                        post_game_started_at = None
                        post_game_waiting_empty = False
                        seeking_new_match = True
                        last_sequence = None
                        detected_player = fixed_player
                        manual_sequence = None
                        initial_storage_sequence = None
                        pending_expected_sequence = None
                        pending_base_sequence = None
                        pending_col = None
                        pending_retry_attempted = False
                        pending_move_started_at = None
                        blocked_sequence = None
                        blocked_sequence_until = 0.0
                        grid_seq_candidate = None
                        grid_seq_candidate_count = 0
                        inferred_move_candidate = None
                        inferred_move_candidate_count = 0
                        last_grid_col_counts = None
                        tracked_grid_sequence = None
                        last_suggested_col = None
                        last_suggested_at = 0.0
                        empty_grid_streak = 0
                        last_solved_sequence = None
                        last_solved_col = None
                        last_logged_suggestion_seq = None
                        last_logged_suggestion_col = None
                        auto_side_probe_started_at = None
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
                            match_active = False
                            post_game_mode = False
                            post_game_started_at = None
                            last_sequence = None
                            detected_player = fixed_player
                            manual_sequence = None
                            initial_storage_sequence = None
                            pending_expected_sequence = None
                            pending_base_sequence = None
                            pending_col = None
                            pending_retry_attempted = False
                            pending_move_started_at = None
                            blocked_sequence = None
                            blocked_sequence_until = 0.0
                            grid_seq_candidate = None
                            grid_seq_candidate_count = 0
                            inferred_move_candidate = None
                            inferred_move_candidate_count = 0
                            last_grid_col_counts = None
                            tracked_grid_sequence = None
                            last_suggested_col = None
                            last_suggested_at = 0.0
                            empty_grid_streak = 0
                            last_solved_sequence = None
                            last_solved_col = None
                            last_logged_suggestion_seq = None
                            last_logged_suggestion_col = None
                            auto_side_probe_started_at = None
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                    def post_game_reload_fallback(reason: str) -> None:
                        nonlocal match_active
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
                        nonlocal post_game_mode
                        nonlocal post_game_started_at
                        nonlocal post_game_waiting_empty
                        nonlocal seeking_new_match

                        print(f"[bridge] Post-game fallback: {reason}; reloading lobby")
                        try:
                            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
                            ensure_bridge_ready(page, config.board_selectors, site_mode)
                        except PlaywrightError as exc:
                            print(f"[bridge] Post-game fallback reload failed: {exc}")

                        # Reset match tracking and queue up new match flow.
                        match_active = False
                        post_game_mode = False
                        post_game_started_at = None
                        post_game_waiting_empty = False
                        seeking_new_match = True
                        last_sequence = None
                        detected_player = fixed_player
                        manual_sequence = None
                        initial_storage_sequence = None
                        pending_expected_sequence = None
                        pending_base_sequence = None
                        pending_col = None
                        pending_retry_attempted = False
                        pending_move_started_at = None
                        blocked_sequence = None
                        blocked_sequence_until = 0.0
                        grid_seq_candidate = None
                        grid_seq_candidate_count = 0
                        inferred_move_candidate = None
                        inferred_move_candidate_count = 0
                        last_grid_col_counts = None
                        tracked_grid_sequence = None
                        last_suggested_col = None
                        last_suggested_at = 0.0
                        empty_grid_streak = 0
                        last_solved_sequence = None
                        last_solved_col = None
                        last_logged_suggestion_seq = None
                        last_logged_suggestion_col = None
                        auto_side_probe_started_at = None

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
                        match_active = False
                        post_game_mode = False
                        post_game_started_at = None
                        last_sequence = None
                        detected_player = fixed_player
                        manual_sequence = None
                        initial_storage_sequence = None
                        pending_expected_sequence = None
                        pending_base_sequence = None
                        pending_col = None
                        pending_retry_attempted = False
                        pending_move_started_at = None
                        blocked_sequence = None
                        blocked_sequence_until = 0.0
                        grid_seq_candidate = None
                        grid_seq_candidate_count = 0
                        inferred_move_candidate = None
                        inferred_move_candidate_count = 0
                        last_grid_col_counts = None
                        tracked_grid_sequence = None
                        last_suggested_col = None
                        last_suggested_at = 0.0
                        empty_grid_streak = 0
                        last_solved_sequence = None
                        last_solved_col = None
                        last_logged_suggestion_seq = None
                        last_logged_suggestion_col = None
                        auto_side_probe_started_at = None
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

                    if in_lobby_url(page.url, args.url) and not has_in_game_ui(page) and not board_signal_ready:
                        now = time.time()
                        if (
                            args.mode == "auto"
                            and site_mode == "papergames"
                            and queued_click_at is not None
                            and (now - queued_click_at) >= QUEUE_RETRY_IDLE_SEC
                        ):
                            print("[bridge] Lobby still idle after queue click; retrying queue")
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
                    print(f"[bridge] Match detected at URL: {page.url}")
                    if probe_source == "storage" and probe_seq is not None:
                        initial_storage_sequence = probe_seq
                        print(f"[bridge] Captured initial storage sequence baseline: {initial_storage_sequence}")

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
                                else:
                                    # Ignore this unstable frame; keep prior tracker.
                                    pass
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
                                    pass

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
                            match_active = False
                            seeking_new_match = True
                            last_sequence = None
                            detected_player = fixed_player
                            manual_sequence = None
                            initial_storage_sequence = None
                            pending_expected_sequence = None
                            pending_base_sequence = None
                            pending_col = None
                            pending_retry_attempted = False
                            pending_move_started_at = None
                            blocked_sequence = None
                            blocked_sequence_until = 0.0
                            grid_seq_candidate = None
                            grid_seq_candidate_count = 0
                            inferred_move_candidate = None
                            inferred_move_candidate_count = 0
                            last_grid_col_counts = None
                            tracked_grid_sequence = None
                            last_suggested_col = None
                            last_suggested_at = 0.0
                            empty_grid_streak = 0
                            last_solved_sequence = None
                            last_solved_col = None
                            last_logged_suggestion_seq = None
                            last_logged_suggestion_col = None
                            auto_side_probe_started_at = None
                            queued_click_at = None
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                    now = time.time()
                    if now - last_wait_log >= 5.0:
                        print("[bridge] Waiting for detectable sequence...")
                        last_wait_log = now
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
                    outcome = {
                        "win1": "Player 1 win",
                        "win2": "Player 2 win",
                        "draw": "Draw",
                    }[seq_status]
                    print(f"[bridge] Game finished: {outcome}")

                    if args.mode == "auto" and site_mode == "papergames":
                        post_game_mode = True
                        post_game_started_at = time.time()
                    else:
                        print("[bridge] Resetting state for next game")
                        match_active = False
                        post_game_waiting_empty = True
                        last_sequence = None
                        detected_player = fixed_player
                        manual_sequence = None
                        initial_storage_sequence = None
                        pending_expected_sequence = None
                        pending_base_sequence = None
                        pending_col = None
                        pending_retry_attempted = False
                        pending_move_started_at = None
                        blocked_sequence = None
                        blocked_sequence_until = 0.0
                        grid_seq_candidate = None
                        grid_seq_candidate_count = 0
                        inferred_move_candidate = None
                        inferred_move_candidate_count = 0
                        last_grid_col_counts = None
                        tracked_grid_sequence = None
                        last_suggested_col = None
                        last_suggested_at = 0.0
                        empty_grid_streak = 0
                        last_solved_sequence = None
                        last_solved_col = None
                        last_logged_suggestion_seq = None
                        last_logged_suggestion_col = None
                        auto_side_probe_started_at = None
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
                            time.sleep(args.poll_ms / 1000.0)
                            continue

                    if move_col_raw is None:
                        time.sleep(args.poll_ms / 1000.0)
                        continue
                    last_solved_sequence = seq
                    last_solved_col = int(move_col_raw)

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
    except PlaywrightTimeoutError as exc:
        print(f"[bridge] Browser timeout: {exc}")
        return 1
    except PlaywrightError as exc:
        print(f"[bridge] Browser error: {exc}")
        return 1
    finally:
        solver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
