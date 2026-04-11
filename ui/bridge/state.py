from dataclasses import dataclass
from typing import Optional


@dataclass
class RuntimeResetState:
    match_active: bool
    post_game_mode: bool
    post_game_started_at: Optional[float]
    last_sequence: Optional[str]
    detected_player: Optional[int]
    manual_sequence: Optional[str]
    initial_storage_sequence: Optional[str]
    blocked_sequence: Optional[str]
    blocked_sequence_until: float
    grid_seq_candidate: Optional[str]
    grid_seq_candidate_count: int
    inferred_move_candidate: Optional[str]
    inferred_move_candidate_count: int
    last_grid_col_counts: Optional[list[int]]
    tracked_grid_sequence: Optional[str]
    last_suggested_col: Optional[int]
    last_suggested_at: float
    empty_grid_streak: int
    last_solved_sequence: Optional[str]
    last_solved_col: Optional[int]
    last_logged_suggestion_seq: Optional[str]
    last_logged_suggestion_col: Optional[int]
    auto_side_probe_started_at: Optional[float]

    @classmethod
    def for_next_match(cls, fixed_player: Optional[int]) -> "RuntimeResetState":
        return cls(
            match_active=False,
            post_game_mode=False,
            post_game_started_at=None,
            last_sequence=None,
            detected_player=fixed_player,
            manual_sequence=None,
            initial_storage_sequence=None,
            blocked_sequence=None,
            blocked_sequence_until=0.0,
            grid_seq_candidate=None,
            grid_seq_candidate_count=0,
            inferred_move_candidate=None,
            inferred_move_candidate_count=0,
            last_grid_col_counts=None,
            tracked_grid_sequence=None,
            last_suggested_col=None,
            last_suggested_at=0.0,
            empty_grid_streak=0,
            last_solved_sequence=None,
            last_solved_col=None,
            last_logged_suggestion_seq=None,
            last_logged_suggestion_col=None,
            auto_side_probe_started_at=None,
        )
