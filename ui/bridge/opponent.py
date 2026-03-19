import re
import time
import unicodedata
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
    def is_allowed_char(ch: str) -> bool:
        # Accept letters/numbers across scripts plus a small punctuation set
        # commonly found in player names.
        if ch in " ._-'()":
            return True
        cat = unicodedata.category(ch)
        if cat.startswith("L") or cat.startswith("N"):
            return True
        # Allow combining marks for accented glyph composition.
        if cat.startswith("M"):
            return True
        return False

    value = raw.strip()
    value = re.sub(r"\s+", " ", value)
    value = _strip_trailing_tag(value)
    # Keep parenthesis removal after tag stripping to avoid truncating "(tag" into
    # an invalid leftover that fails sanitization.
    value = value.strip("-:|[]{}")
    value = _strip_rank_suffix(value)
    if len(value) < 2 or len(value) > 32:
        return None
    if not all(is_allowed_char(ch) for ch in value):
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
    """Parse opponent username from structured room-player profile lines only."""

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

                  const addUnique = (arr, v) => {
                    if (!v) return;
                    if (!arr.includes(v)) arr.push(v);
                  };

                  const roomPlayerProfiles = [];
                  const roomPlayers = document.querySelector('app-room-players');
                  if (roomPlayers) {
                    const profileEls = Array.from(
                      roomPlayers.querySelectorAll(
                        'span[appprofileopener], span[appProfileOpener], [appprofileopener], [appProfileOpener]'
                      )
                    );
                    for (const el of profileEls) {
                      const txt = pull(
                        el.innerText ||
                        el.textContent ||
                        el.getAttribute('data-username') ||
                        el.getAttribute('title') ||
                        ''
                      );
                      addUnique(roomPlayerProfiles, txt);
                    }
                  }

                  return { roomPlayerProfiles };
                }
                """
            )
        except PlaywrightError:
            return None

    def pick_from_raw(raw) -> Optional[str]:
        if not isinstance(raw, dict):
            return None
        room_player_profiles = raw.get("roomPlayerProfiles")
        if isinstance(room_player_profiles, list):
            for p in room_player_profiles:
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

    # Header/profile elements may appear shortly after board attach.
    time.sleep(0.12)
    second = try_pick_once()
    if second is not None:
        return second

    return None
