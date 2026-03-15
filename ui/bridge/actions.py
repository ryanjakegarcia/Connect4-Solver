import time
from typing import Optional

from playwright.sync_api import Error as PlaywrightError


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
    return click_button_by_text_tokens(page, ["rematch"])


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
