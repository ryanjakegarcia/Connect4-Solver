from typing import Callable, Optional

from bridge.stats import BridgeStats


def record_game_result(
    *,
    stats: BridgeStats,
    mapped_result: Optional[str],
    detected_player: Optional[int],
    current_opponent: Optional[str],
    sequence_len: int,
    game_solve_samples: int,
    game_solve_total_sec: float,
    game_opponent_move_samples: int,
    game_opponent_move_total_sec: float,
    on_win_emote: Optional[Callable[[Optional[str], str], None]] = None,
    emote_context: str = "",
) -> bool:
    if mapped_result is None:
        return False

    stats.record_game(
        mapped_result,
        detected_player,
        current_opponent,
        sequence_len,
        game_solve_samples,
        game_solve_total_sec,
        game_opponent_move_samples,
        game_opponent_move_total_sec,
    )
    if on_win_emote is not None:
        on_win_emote(mapped_result, emote_context)
    return True
