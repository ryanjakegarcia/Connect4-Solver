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


def read_site_wld_record(page) -> Optional[dict]:
        """Best-effort parse of website-visible W-L-D counters."""
        try:
                dom_wld = page.evaluate(r"""
                        () => {
                            const asInt = (el) => {
                                if (!el) return null;
                                const txt = (el.textContent || '').trim();
                                return /^\d+$/.test(txt) ? parseInt(txt, 10) : null;
                            };

                            // Prefer the row containing the tooltip icon: "Wins / Losses / Draws".
                            const icon = document.querySelector('fa-icon[mattooltip*="Wins"], fa-icon[mattooltip*="wins"]');
                            const container = icon ? icon.closest('div') : null;
                            if (container) {
                                const winEl = container.querySelector('span.text-success');
                                const lossEl = container.querySelector('span.text-danger');
                                const allNums = Array.from(container.querySelectorAll('span.mat-mdc-tooltip-trigger'));
                                const win = asInt(winEl);
                                const loss = asInt(lossEl);
                                const fallbackNums = allNums.map(asInt).filter((v) => Number.isInteger(v));
                                const drawsFromTail = fallbackNums.length >= 3 ? fallbackNums[2] : null;
                                const draw = Number.isInteger(drawsFromTail) ? drawsFromTail : null;
                                if (Number.isInteger(win) && Number.isInteger(loss) && Number.isInteger(draw)) {
                                    return { wins: win, losses: loss, draws: draw, source: 'dom-tooltip-row' };
                                }
                            }

                            // Secondary DOM strategy: find any compact "N / N / N" block.
                            const compact = Array.from(document.querySelectorAll('div, span')).find((el) => {
                                const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
                                return /^\d+\s*\/\s*\d+\s*\/\s*\d+$/.test(t);
                            });
                            if (compact) {
                                const t = (compact.textContent || '').replace(/\s+/g, ' ').trim();
                                const m = t.match(/^(\d+)\s*\/\s*(\d+)\s*\/\s*(\d+)$/);
                                if (m) {
                                    return {
                                        wins: parseInt(m[1], 10),
                                        losses: parseInt(m[2], 10),
                                        draws: parseInt(m[3], 10),
                                        source: 'dom-compact-slash',
                                    };
                                }
                            }

                            return null;
                        }
                """)
        except PlaywrightError:
                dom_wld = None

        if isinstance(dom_wld, dict):
                try:
                        return {
                                "wins": int(dom_wld.get("wins")),
                                "losses": int(dom_wld.get("losses")),
                                "draws": int(dom_wld.get("draws")),
                                "source": str(dom_wld.get("source") or "dom"),
                        }
                except Exception:
                        pass

        try:
            body_text = page.evaluate("() => (document.body?.innerText || '')")
        except PlaywrightError:
            return None

        if not isinstance(body_text, str) or not body_text.strip():
            return None

        normalized = re.sub(r"\s+", " ", body_text.lower()).strip()

        patterns = [
            re.compile(r"\bw\s*[-:/|]\s*l\s*[-:/|]\s*d\s*[:=]?\s*(\d+)\s*[-/|]\s*(\d+)\s*[-/|]\s*(\d+)"),
            re.compile(r"\b(\d+)\s*[-/|]\s*(\d+)\s*[-/|]\s*(\d+)\s*(?:w\s*[-/]?\s*l\s*[-/]?\s*d|wins?\s*losses?\s*draws?)"),
            re.compile(r"\bwins?\s*[:=]?\s*(\d+)\D{1,25}loss(?:es)?\s*[:=]?\s*(\d+)\D{1,25}draws?\s*[:=]?\s*(\d+)"),
        ]

        for idx, pat in enumerate(patterns):
            m = pat.search(normalized)
            if m is None:
                continue
            wins, losses, draws = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return {
                "wins": wins,
                "losses": losses,
                "draws": draws,
                "source": f"pattern-{idx + 1}",
            }

        return None


def detect_terminal_page_reason(page) -> Optional[str]:
    try:
        reason = page.evaluate(r"""
            () => {
                            const normalize = (s) => (s || '')
                                .toLowerCase()
                                .replace(/[\u2019']/g, "'")
                                .replace(/\s+/g, ' ')
                                .trim();

                            const bodyText = (document.body?.innerText || '');
                            const lines = bodyText
                                .split(/\r?\n/)
                                .map((s) => normalize(s))
                                .filter((s) => s.length > 0);

                            // Exclude visible profile-name text so usernames like
                            // "Yup You Lost" do not trigger terminal-state parsing.
                            const profileTexts = new Set(
                                Array.from(
                                    document.querySelectorAll(
                                        '[appprofileopener], [appProfileOpener], [data-username], app-room-players [title], app-room-players span'
                                    )
                                )
                                    .map((el) => normalize(el.innerText || el.textContent || el.getAttribute('data-username') || el.getAttribute('title') || ''))
                                    .filter((s) => s.length > 0)
                            );

                            const filteredLines = lines.filter((line) => !profileTexts.has(line));

                            const matchLine = (re) => filteredLines.some((line) => re.test(line));

                            if (
                                matchLine(/^you won([!.]|$)/) ||
                                matchLine(/^you win([!.]|$)/) ||
                                matchLine(/^you have won([!.]|$)/) ||
                                matchLine(/\bvictory\b/)
                            ) return 'you won';
                            if (
                                matchLine(/^you lost([!.]|$)/) ||
                                matchLine(/^you lose([!.]|$)/) ||
                                matchLine(/^you have lost([!.]|$)/) ||
                                matchLine(/\bdefeat\b/)
                            ) return 'you lost';
                            if (
                                matchLine(/^draw([!.]|$)/) ||
                                matchLine(/^it's a draw([!.]|$)/) ||
                                matchLine(/^its a draw([!.]|$)/) ||
                                matchLine(/^game drawn([!.]|$)/)
                            ) return 'draw';

                            if (matchLine(/\bopponent left\b/) || matchLine(/\bopponent quit\b/) || matchLine(/\bopponent has left\b/)) return 'opponent left';
                            if (matchLine(/\bopponent disconnected\b/)) return 'opponent disconnected';
                            if (matchLine(/\bopponent aborted\b/)) return 'opponent aborted';
                            if (matchLine(/\bleft the game\b/)) return 'opponent left';
                            if (matchLine(/\bdisconnected\b/)) return 'disconnected';
                            if (matchLine(/^timed out\b/) || matchLine(/^timeout\b/) || matchLine(/\btime out\b/)) return 'timed out';
                            if (
                                matchLine(/\byou (have )?(resigned|surrendered|forfeited)\b/) ||
                                matchLine(/^you gave up([!.]|$)/)
                            ) return 'you resigned';
                            if (
                                matchLine(/\bopponent (has )?(resigned|surrendered|forfeited)\b/) ||
                                matchLine(/\bopponent gave up\b/)
                            ) return 'opponent resigned';
                            if (matchLine(/^game over([!.]|$)/)) return 'game over';
                            if (matchLine(/\bcancelled\b|\bcanceled\b/)) return 'canceled';

                            // Last-resort broad checks for abort/disconnect pages where text is sparse.
                            const flat = filteredLines.join('\n');
                            if (/\bopponent (left|disconnected|aborted|quit)\b/.test(flat)) return 'opponent disconnected';
                            if (/\bleft the game\b/.test(flat)) return 'opponent left';
                            if (/\bdisconnected\b/.test(flat)) return 'disconnected';
                            if (/\btimed out\b|\btimeout\b/.test(flat)) return 'timed out';
                            if (/\b(resigned|surrendered|forfeited|gave up)\b/.test(flat)) {
                                if (/\byou\b.*\b(resigned|surrendered|forfeited|gave up)\b/.test(flat)) return 'you resigned';
                                if (/\byou\b.*\b(win|won)\b/.test(flat) || /\bvictory\b/.test(flat)) return 'you won';
                                if (/\byou\b.*\b(lose|lost)\b/.test(flat) || /\bdefeat\b/.test(flat)) return 'you lost';
                                if (/\bopponent\b.*\b(resigned|surrendered|forfeited|gave up)\b/.test(flat)) return 'opponent resigned';
                            }

                            return null;
            }
        """)
    except PlaywrightError:
        return None

    return reason if isinstance(reason, str) else None


def read_terminal_page_text_snapshot(page) -> Optional[dict]:
        """Capture normalized terminal text diagnostics for telemetry/debugging."""
        try:
                raw = page.evaluate(r"""
                        () => {
                            const normalize = (s) => (s || '')
                                .toLowerCase()
                                .replace(/[\u2019']/g, "'")
                                .replace(/\s+/g, ' ')
                                .trim();

                            const bodyText = (document.body?.innerText || '');
                            const lines = bodyText
                                .split(/\r?\n/)
                                .map((s) => normalize(s))
                                .filter((s) => s.length > 0);

                            const profileTexts = Array.from(
                                document.querySelectorAll(
                                    '[appprofileopener], [appProfileOpener], [data-username], app-room-players [title], app-room-players span'
                                )
                            )
                                .map((el) => normalize(el.innerText || el.textContent || el.getAttribute('data-username') || el.getAttribute('title') || ''))
                                .filter((s) => s.length > 0);

                            const profileSet = new Set(profileTexts);
                            const filteredLines = lines.filter((line) => !profileSet.has(line));

                            return {
                                lines: lines.slice(0, 80),
                                filteredLines: filteredLines.slice(0, 80),
                                profileTexts: profileTexts.slice(0, 40),
                            };
                        }
                """)
        except PlaywrightError:
                return None

        if not isinstance(raw, dict):
                return None
        return {
                "lines": raw.get("lines") if isinstance(raw.get("lines"), list) else [],
                "filtered_lines": raw.get("filteredLines") if isinstance(raw.get("filteredLines"), list) else [],
                "profile_texts": raw.get("profileTexts") if isinstance(raw.get("profileTexts"), list) else [],
        }


def read_post_game_ui_state(page) -> Optional[dict]:
    try:
        raw = page.evaluate(r"""
            () => {
              const text = (document.body?.innerText || '').toLowerCase();
              const clickable = Array.from(document.querySelectorAll('button, [role="button"], a, .btn, [class*="button"]'));
              const labels = clickable.map((el) => (el.innerText || el.textContent || '').toLowerCase()).join('\n');
              return {
                hasRematch: labels.includes('rematch'),
                hasLeaveRoom: labels.includes('leave room') || !!document.querySelector('[aria-label="Leave room"]'),
                                opponentLeft: text.includes('opponent left') ||
                                                            text.includes('opponent disconnected') ||
                                                            text.includes('opponent aborted') ||
                                                            text.includes('opponent quit') ||
                                                            text.includes('opponent has left') ||
                                                            text.includes('left the game') ||
                                                            text.includes('disconnected'),
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
