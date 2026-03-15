import re
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

VALID_SEQ_RE = re.compile(r"^[1-7]*$")


def is_our_turn(move_sequence: str, player: int) -> bool:
    if player == 1:
        return len(move_sequence) % 2 == 0
    return len(move_sequence) % 2 == 1


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
    """Infer one newly added move from per-column count deltas."""
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
        if source == "storage" and initial_storage_sequence is not None and seq == initial_storage_sequence:
            return None, manual_sequence, None
        return seq, seq, source

    if not manual_fallback:
        return None, manual_sequence, None

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

    if manual_sequence is None:
        seed = input("[bridge] Enter known sequence to seed tracking (or blank to skip): ").strip()
        if not seed:
            return None, manual_sequence, None
        if not VALID_SEQ_RE.fullmatch(seed):
            print("[bridge] Invalid sequence format; expected digits 1-7 only")
            return None, manual_sequence, None
        print("[bridge] sequence source=manual-seed")
        return seed, seed, "manual"

    if detected_player is not None and is_our_turn(manual_sequence, detected_player):
        return manual_sequence, manual_sequence, "manual"

    raw = input("[bridge] Opponent move [1-7], blank=skip, u=undo, r=reset, =SEQ to replace: ").strip()

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
