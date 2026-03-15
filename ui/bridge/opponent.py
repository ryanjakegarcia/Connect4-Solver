import re
import time
from typing import Optional

from playwright.sync_api import Error as PlaywrightError


RANK_SUFFIX_RE = re.compile(r"\s+\d{3,6}$")
TRAILING_TAG_RE = re.compile(r"\s*\(([^)]*)\)$")
UI_TAG_KEYWORDS = {
    "streak",
    "losing",
    "winning",
    "online",
    "offline",
    "disconnected",
    "aborted",
    "left",
}


def _strip_rank_suffix(value: str) -> str:
    """Papergames often appends rank/ELO digits (e.g. 'name 7850')."""
    return RANK_SUFFIX_RE.sub("", value).strip()


def _strip_trailing_tag(value: str) -> str:
    """Drop known UI descriptors appended after username.

    Keep parenthetical suffixes that can be part of a real username, such as
    country tags like "(PL)".
    """
    m = TRAILING_TAG_RE.search(value)
    if not m:
        return value

    tag = m.group(1).strip().lower()
    if not tag:
        return TRAILING_TAG_RE.sub("", value).strip()

    if any(keyword in tag for keyword in UI_TAG_KEYWORDS):
        return TRAILING_TAG_RE.sub("", value).strip()

    return value


def sanitize_username(raw: str) -> Optional[str]:
    value = raw.strip()
    value = re.sub(r"\s+", " ", value)
    value = _strip_trailing_tag(value)
    # Keep parenthesis removal after tag stripping to avoid truncating "(tag" into
    # an invalid leftover that fails sanitization.
    value = value.strip("-:|[]{}")
    value = _strip_rank_suffix(value)
    if len(value) < 2 or len(value) > 32:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.\- ()]+", value):
        return None
    lower = value.lower()
    blocked = {
        "opponent",
        "you",
        "player",
        "random player",
        "leave room",
        "rematch",
        "disconnected",
        "aborted",
        "canceled",
        "cancelled",
    }
    if lower in blocked:
        return None
    return value


def canonical_username(raw: Optional[str]) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    sanitized = sanitize_username(raw)
    if sanitized is None:
        return None
    return sanitized.lower()


def usernames_equivalent(a: Optional[str], b: Optional[str]) -> bool:
    a_norm = canonical_username(a)
    b_norm = canonical_username(b)
    return a_norm is not None and b_norm is not None and a_norm == b_norm


def read_opponent_username_strict(page, our_username: Optional[str] = None) -> Optional[str]:
    """Best-effort strict parser: only return username when tied to explicit opponent labels."""

    def pick_name(name: Optional[str]) -> Optional[str]:
        if not isinstance(name, str):
            return None
        s = sanitize_username(name)
        if s is None:
            return None
        if usernames_equivalent(s, our_username):
            return None
        return s

    def extract_from_context(ctx):
        try:
            return ctx.evaluate(
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

                            // Highest-confidence path: profile names that appear in an
                            // opponent-labeled container/sibling context.
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

                                // Some layouts separate the label and name into nearby siblings.
                                const parent = anchor.parentElement;
                                if (parent) {
                                    const nearby = Array.from(parent.querySelectorAll(nameSel));
                                    for (const el of nearby) {
                                        const txt = pull(el.innerText || el.textContent || el.getAttribute('data-username') || el.getAttribute('title') || '');
                                        addUnique(opponentProfiles, txt);
                                    }
                                }
                            }

                            // Fallback: names scoped to live game container only.
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

                                                    // High-confidence structured extraction for papergames player header.
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
                            }


                            // Fallback: visible profile-like labels near the board in viewport space.
                            // This catches layouts where player names live outside #game but still close
                            // to the board header area, while avoiding distant leaderboard entries.
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
              const candidates = Array.from(
                document.querySelectorAll('[aria-label], [class*="opponent" i], [id*="opponent" i]')
              );
              for (const el of candidates) {
                const txt = ((el.getAttribute('aria-label') || '') + ' ' + (el.innerText || el.textContent || '')).trim();
                const m = txt.match(labelRegex);
                if (m && m[1]) {
                  const c = pull(m[1]);
                                    if (c) return { labelCandidate: c, opponentProfiles, roomProfiles, roomPlayerProfiles, boardNearbyProfiles };
                }
              }

                            return { labelCandidate, opponentProfiles, roomProfiles, roomPlayerProfiles, boardNearbyProfiles };
            }
            """
            )
        except PlaywrightError:
            return None

    def pick_from_raw(raw) -> Optional[str]:
        if isinstance(raw, str):
            return pick_name(raw)

        if not isinstance(raw, dict):
            return None

        from_label = pick_name(raw.get("labelCandidate"))
        if from_label is not None:
            return from_label

        opponent_profiles = raw.get("opponentProfiles")
        if isinstance(opponent_profiles, list):
            for p in opponent_profiles:
                picked = pick_name(p if isinstance(p, str) else None)
                if picked is not None:
                    return picked

        room_profiles = raw.get("roomProfiles")
        if isinstance(room_profiles, list):
            for p in room_profiles:
                picked = pick_name(p if isinstance(p, str) else None)
                if picked is not None:
                    return picked

        room_player_profiles = raw.get("roomPlayerProfiles")
        if isinstance(room_player_profiles, list):
            for p in room_player_profiles:
                picked = pick_name(p if isinstance(p, str) else None)
                if picked is not None:
                    return picked

        board_nearby_profiles = raw.get("boardNearbyProfiles")
        if isinstance(board_nearby_profiles, list):
            for p in board_nearby_profiles:
                picked = pick_name(p if isinstance(p, str) else None)
                if picked is not None:
                    return picked

        return None

    contexts = [page]
    try:
        main_frame = page.main_frame
        for frame in page.frames:
            if frame is main_frame:
                continue
            contexts.append(frame)
    except Exception:
        pass

    def try_pick_once() -> Optional[str]:
        for ctx in contexts:
            raw = extract_from_context(ctx)
            picked = pick_from_raw(raw)
            if picked is not None:
                return picked
        return None

    first = try_pick_once()
    if first is not None:
        return first

    # Match header/profile elements can appear a fraction later than board attach.
    time.sleep(0.12)
    second = try_pick_once()
    if second is not None:
        return second

    return None
