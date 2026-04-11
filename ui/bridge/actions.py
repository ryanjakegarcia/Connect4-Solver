import time
from typing import Optional

from playwright.sync_api import Error as PlaywrightError


def click_column_dom(page, col: int) -> bool:
    try:
        ok = page.evaluate("(c) => window.__c4Bridge.clickColumnDom(c)", col)
    except PlaywrightError:
        return False
    return bool(ok)


def play_column(page, col: int) -> Optional[str]:
    if click_column_dom(page, col):
        return "dom"
    return None


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
    try:
        page.click('[aria-label="Leave room"]', timeout=400)
        return True
    except Exception:
        pass

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
    return click_button_by_text_tokens(page, ["play", "again"])


def click_emoji_by_code(page, emoji_code: str) -> bool:
        """Click a visible emoji button by its hex code (for example: 1f60e)."""
        normalized = (emoji_code or "").strip().lower()
        if not normalized:
                return False

        try:
                ok = page.evaluate(
                        """
                        async (code) => {
                            const normalized = String(code || '').toLowerCase().replace(/^u\+/, '').replace(/\.png$/, '');
                            if (!normalized) return false;

                            const isVisible = (el) => {
                                if (!el) return false;
                                const r = el.getBoundingClientRect();
                                if (r.width <= 0 || r.height <= 0) return false;
                                const style = window.getComputedStyle(el);
                                return style.visibility !== 'hidden' && style.display !== 'none';
                            };

                            const isEnabled = (el) => {
                                if (!el) return false;
                                const disabled = (el.getAttribute('disabled') !== null) ||
                                    (el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                                return !disabled;
                            };

                            const getVisibleEmojiButtons = () => {
                                const imgs = Array.from(document.querySelectorAll('img.emoji-icon, img[alt="Emoji"], img[src*="/emoji/"]'));
                                const unique = new Set();
                                const out = [];
                                for (const img of imgs) {
                                    const src = String(img.getAttribute('src') || img.src || '').toLowerCase();
                                    if (!src.includes('/emoji/')) continue;
                                    const btn = img.closest('button, [role="button"], a');
                                    if (!btn || !isVisible(btn) || !isEnabled(btn)) continue;
                                    if (unique.has(btn)) continue;
                                    unique.add(btn);
                                    out.push(btn);
                                }
                                return out;
                            };

                            const isMenuLikelyOpen = () => getVisibleEmojiButtons().length >= 4;

                            const clickTargetEmoji = () => {
                                const imgs = Array.from(document.querySelectorAll('img.emoji-icon, img[alt="Emoji"], img[src*="/emoji/"]'));
                                const emojiButtons = getVisibleEmojiButtons();
                                for (const img of imgs) {
                                    const src = String(img.getAttribute('src') || img.src || '').toLowerCase();
                                    if (!src.includes('/emoji/')) continue;
                                    if (!(src.endsWith(`/${normalized}.png`) || src.includes(`/emoji/${normalized}.png`))) continue;

                                    const button = img.closest('button, [role="button"], a');
                                    if (!button || !isVisible(button) || !isEnabled(button)) continue;

                                    // Avoid false positives: with menu closed there is often only one
                                    // visible emoji button (the trigger), not a selectable reaction.
                                    if (!isMenuLikelyOpen() && emojiButtons.length <= 1) continue;

                                    try {
                                        button.click();
                                        return true;
                                    } catch (_) {}

                                    if (isVisible(img)) {
                                        try {
                                            img.click();
                                            return true;
                                        } catch (_) {}
                                    }
                                }
                                return false;
                            };

                            const clickEmoteMenuTrigger = () => {
                                const emojiButtons = getVisibleEmojiButtons();
                                if (emojiButtons.length >= 4) return true; // already open

                                // Most reliable closed-state trigger: single visible emoji icon button.
                                if (emojiButtons.length === 1) {
                                    try {
                                        emojiButtons[0].click();
                                        return true;
                                    } catch (_) {}
                                }

                                const allButtons = Array.from(document.querySelectorAll('button, [role="button"]'));
                                const scored = [];

                                for (const btn of allButtons) {
                                    if (!isVisible(btn) || !isEnabled(btn)) continue;

                                    const label = (
                                        (btn.getAttribute('aria-label') || '') + ' ' +
                                        (btn.getAttribute('title') || '') + ' ' +
                                        (btn.innerText || btn.textContent || '')
                                    ).toLowerCase();

                                    const hasTouchTarget = !!btn.querySelector('span.mat-mdc-button-touch-target');
                                    const hasEmojiChild = !!btn.querySelector('img.emoji-icon, img[alt="Emoji"], img[src*="/emoji/"]');
                                    const className = String(btn.className || '').toLowerCase();

                                    const looksLikeEmote = label.includes('emoji') || label.includes('emote') || label.includes('reaction');
                                    const looksLikeSettings =
                                        label.includes('setting') ||
                                        label.includes('option') ||
                                        label.includes('config') ||
                                        label.includes('profile') ||
                                        label.includes('account') ||
                                        label.includes('sound') ||
                                        label.includes('music');

                                    let score = 0;
                                    if (looksLikeEmote) score += 100;
                                    if (hasEmojiChild) score += 60;
                                    if (hasTouchTarget) score += 8;
                                    if (className.includes('mat-mdc-icon-button')) score += 5;
                                    if (looksLikeSettings) score -= 120;

                                    if (score > 0) scored.push({ btn, score });
                                }

                                scored.sort((a, b) => b.score - a.score);
                                for (const item of scored) {
                                    try {
                                        item.btn.click();
                                        return true;
                                    } catch (_) {}
                                }

                                // Last-resort fallback based on touch-target marker, but only for
                                // candidates that look emoji-related.
                                const touchTargets = Array.from(document.querySelectorAll('span.mat-mdc-button-touch-target'));
                                for (const span of touchTargets) {
                                    const btn = span.closest('button, [role="button"]');
                                    if (!btn || !isVisible(btn) || !isEnabled(btn)) continue;
                                    const label = (
                                        (btn.getAttribute('aria-label') || '') + ' ' +
                                        (btn.getAttribute('title') || '') + ' ' +
                                        (btn.innerText || btn.textContent || '')
                                    ).toLowerCase();
                                    const hasEmojiChild = !!btn.querySelector('img.emoji-icon, img[alt="Emoji"], img[src*="/emoji/"]');
                                    const looksLikeEmote = label.includes('emoji') || label.includes('emote') || label.includes('reaction');
                                    if (!hasEmojiChild && !looksLikeEmote) continue;
                                    try {
                                        btn.click();
                                        return true;
                                    } catch (_) {}
                                }

                                return false;
                            };

                            if (clickTargetEmoji()) return true;
                            if (!clickEmoteMenuTrigger()) return false;

                            await new Promise((resolve) => setTimeout(resolve, 160));
                            return clickTargetEmoji();
                        }
                        """,
                        normalized,
                )
        except PlaywrightError:
                return False

        return bool(ok)


def click_play_online_random(page) -> bool:
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
    """Try common papergames queue flows."""
    if click_play_online_random(page):
        return True
    if click_random_player(page):
        return True
    if click_play_online(page):
        time.sleep(0.15)
        if click_random_player(page):
            return True
        return False
    return False
