from typing import Optional


def build_read_sequence_kwargs(
    *,
    game_url: str,
    manual_fallback: bool,
    manual_mode: str,
    manual_sequence: Optional[str],
    detected_player: Optional[int],
    initial_storage_sequence: Optional[str],
) -> dict:
    """Build read_sequence keyword args in one place for consistency."""
    return {
        "manual_fallback": manual_fallback,
        "game_url": game_url,
        "manual_mode": manual_mode,
        "manual_sequence": manual_sequence,
        "detected_player": detected_player,
        "initial_storage_sequence": initial_storage_sequence,
    }


def maybe_update_manual_sequence_after_play(
    *,
    manual_fallback: bool,
    manual_mode: str,
    current_sequence: str,
    played_col_zero_based: int,
) -> Optional[str]:
    if not manual_fallback or manual_mode != "incremental":
        return None
    return current_sequence + str(played_col_zero_based + 1)
