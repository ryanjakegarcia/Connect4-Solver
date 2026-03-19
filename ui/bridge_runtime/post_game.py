import time
from dataclasses import dataclass
from typing import Callable, Optional

from playwright.sync_api import Error as PlaywrightError


@dataclass
class PostGameFlowResult:
    handled: bool = False
    post_game_started_at: Optional[float] = None
    last_lifecycle_log: Optional[float] = None
    last_post_game_action_attempt_at: Optional[float] = None
    post_game_waiting_empty: Optional[bool] = None
    seeking_new_match: Optional[bool] = None


def handle_post_game_flow(
    *,
    mode: str,
    site_mode: str,
    page,
    game_url: str,
    poll_sec: float,
    auto_rematch: bool,
    post_game_reload_sec: float,
    post_game_wait_sec_runtime: float,
    post_game_mode: bool,
    post_game_started_at: Optional[float],
    last_lifecycle_log: float,
    last_post_game_action_attempt_at: float,
    post_game_waiting_empty: bool,
    action_retry_gap_sec: float,
    debug_enabled: bool,
    board_selectors: list[str],
    ensure_bridge_ready_fn: Callable[[list[str], str], bool],
    in_lobby_url_fn: Callable[[str], bool],
    is_live_room_url_fn: Callable[[str], bool],
    has_in_game_ui_fn: Callable[[], bool],
    click_rematch_fn: Callable[[], bool],
    click_leave_room_fn: Callable[[], bool],
    reset_runtime_for_next_match_fn: Callable[..., None],
) -> PostGameFlowResult:
    """Handle auto papergames post-game action flow.

    Returns a result where handled=True means caller should sleep and continue.
    """
    result = PostGameFlowResult()

    def debug(msg: str) -> None:
        if debug_enabled:
            print(f"[bridge][debug] {msg}")

    if not (mode == "auto" and site_mode == "papergames" and post_game_mode):
        return result

    now = time.time()
    if post_game_started_at is None:
        post_game_started_at = now
        result.post_game_started_at = post_game_started_at
    post_game_elapsed = now - post_game_started_at
    debug(
        "post_game_flow "
        f"url={page.url} elapsed={post_game_elapsed:.1f}s "
        f"wait_target={post_game_wait_sec_runtime:.1f}s"
    )

    in_lobby = in_lobby_url_fn(page.url)
    in_live_room = is_live_room_url_fn(page.url)

    # Once we are off the live room, wait on home/queue before re-queuing.
    if in_lobby or not in_live_room:
        debug("post_game_branch=home_wait_or_queue")
        if post_game_elapsed < post_game_wait_sec_runtime:
            if now - last_lifecycle_log >= 2.0:
                print(
                    "[bridge] Terminal wait on home before queue... "
                    f"({post_game_elapsed:.1f}/{post_game_wait_sec_runtime:.1f}s)"
                )
                result.last_lifecycle_log = now
            result.handled = True
            return result

        if in_lobby:
            print("[bridge] Post-game redirect to lobby detected; queueing next match")
        else:
            print("[bridge] Post-game room exited; queueing next match")

        reset_runtime_for_next_match_fn(
            seeking_new_match_value=True,
            post_game_waiting_empty_value=False,
        )
        result.handled = True
        return result

    acted = False
    if now - last_post_game_action_attempt_at >= action_retry_gap_sec:
        result.last_post_game_action_attempt_at = now
        debug("post_game_branch=attempt_in_room_action")
        if auto_rematch:
            acted = click_rematch_fn()
            if acted:
                debug("post_game_action=rematch_clicked")
                print("[bridge] Clicking rematch")
                result.post_game_waiting_empty = True
        else:
            acted = click_leave_room_fn()
            if acted:
                debug("post_game_action=leave_room_clicked")
                print("[bridge] Leaving room to find new match")
                # Start wait window after leave action; queueing occurs on home/queue routes.
                result.post_game_started_at = now

    if not acted:
        try:
            in_game_ui = has_in_game_ui_fn()
        except Exception:
            in_game_ui = False
        debug(f"post_game_action_not_taken in_game_ui={in_game_ui}")

    if acted:
        if auto_rematch:
            reset_runtime_for_next_match_fn()
        result.handled = True
        return result

    if post_game_reload_sec > 0 and post_game_elapsed >= post_game_reload_sec:
        print("[bridge] Post-game fallback: action timeout; reloading lobby")
        try:
            page.goto(game_url, wait_until="domcontentloaded", timeout=60000)
            ensure_bridge_ready_fn(board_selectors, site_mode)
        except PlaywrightError as exc:
            print(f"[bridge] Post-game fallback reload failed: {exc}")

        reset_runtime_for_next_match_fn(
            seeking_new_match_value=True,
            post_game_waiting_empty_value=False,
        )
        result.handled = True
        return result

    if now - last_lifecycle_log >= 5.0:
        print(
            "[bridge] Waiting for post-game action target... "
            f"({post_game_elapsed:.1f}s)"
        )
        result.last_lifecycle_log = now

    result.handled = True
    return result
