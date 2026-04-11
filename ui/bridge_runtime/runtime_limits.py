from dataclasses import dataclass
from typing import Optional


@dataclass
class RuntimeLimitDecision:
    should_force_quit: bool = False
    should_quit_now: bool = False
    should_request_drain: bool = False
    reason: Optional[str] = None
    elapsed_sec: float = 0.0


def evaluate_runtime_limits(
    *,
    mode: str,
    elapsed_sec: float,
    soft_limit_sec: Optional[float],
    hard_limit_sec: Optional[float],
    soft_already_triggered: bool,
    hard_already_triggered: bool,
    post_game_mode: bool,
    auto_control_state: str,
    match_active: bool,
    in_live_room: bool,
) -> RuntimeLimitDecision:
    if mode not in {"auto", "standby"}:
        return RuntimeLimitDecision(elapsed_sec=elapsed_sec)

    if hard_limit_sec is not None and not hard_already_triggered and elapsed_sec >= hard_limit_sec:
        return RuntimeLimitDecision(
            should_force_quit=True,
            reason="hard-timeout-force-quit",
            elapsed_sec=elapsed_sec,
        )

    if soft_limit_sec is None or soft_already_triggered or elapsed_sec < soft_limit_sec:
        return RuntimeLimitDecision(elapsed_sec=elapsed_sec)

    if post_game_mode:
        return RuntimeLimitDecision(
            should_quit_now=True,
            reason="soft-timeout-post-game",
            elapsed_sec=elapsed_sec,
        )

    if auto_control_state == "paused":
        return RuntimeLimitDecision(
            should_quit_now=True,
            reason="soft-timeout-paused",
            elapsed_sec=elapsed_sec,
        )

    if not match_active and not in_live_room:
        return RuntimeLimitDecision(
            should_quit_now=True,
            reason="soft-timeout-no-active-game",
            elapsed_sec=elapsed_sec,
        )

    return RuntimeLimitDecision(
        should_request_drain=True,
        reason="soft-timeout-drain-requested",
        elapsed_sec=elapsed_sec,
    )
