from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional, Sequence, Tuple


class PendingMoveState(str, Enum):
    IDLE = "idle"
    AWAIT_COMMIT = "await_commit"
    CONFIRMED_PROGRESSING = "confirmed_progressing"
    RETRY_PENDING = "retry_pending"
    HARD_DIVERGED = "hard_diverged"
    EXPIRED = "expired"


class PendingMoveOutcome(str, Enum):
    WAIT = "wait"
    CONFIRMED = "confirmed"
    RETRY = "retry"
    DIVERGED = "diverged"
    EXPIRED = "expired"


@dataclass(frozen=True)
class PendingMoveContext:
    base_seq: str
    base_counts: Tuple[int, int, int, int, int, int, int]
    clicked_col: int
    expected_seq: str
    expected_counts: Tuple[int, int, int, int, int, int, int]
    started_at: float
    retry_attempted: bool = False


@dataclass(frozen=True)
class PendingObservation:
    counts: Tuple[int, int, int, int, int, int, int]
    timestamp: float


@dataclass(frozen=True)
class PendingEvaluation:
    state: PendingMoveState
    outcome: PendingMoveOutcome
    reason: str
    elapsed_sec: float
    our_move_proved: bool
    expected_reached: bool
    extra_since_expected: int


def _validate_counts(counts: Sequence[int]) -> bool:
    return (
        len(counts) == 7
        and all(isinstance(x, int) and 0 <= x <= 6 for x in counts)
    )


def _to_counts_tuple(counts: Sequence[int]) -> Tuple[int, int, int, int, int, int, int]:
    if not _validate_counts(counts):
        raise ValueError("counts must be 7 ints in [0, 6]")
    return tuple(int(x) for x in counts)  # type: ignore[return-value]


def build_column_counts_from_sequence(sequence: str) -> Tuple[int, int, int, int, int, int, int]:
    counts = [0] * 7
    for ch in sequence:
        if ch < "1" or ch > "7":
            raise ValueError("sequence must contain only digits 1-7")
        idx = ord(ch) - ord("1")
        counts[idx] += 1
        if counts[idx] > 6:
            raise ValueError("sequence is invalid: a column exceeds 6 tokens")
    return tuple(counts)  # type: ignore[return-value]


def make_pending_context(base_seq: str, clicked_col: int, started_at: float) -> PendingMoveContext:
    if not (0 <= clicked_col <= 6):
        raise ValueError("clicked_col must be in [0, 6]")

    base_counts = list(build_column_counts_from_sequence(base_seq))
    if base_counts[clicked_col] >= 6:
        raise ValueError("clicked column is already full in base sequence")

    expected_counts = list(base_counts)
    expected_counts[clicked_col] += 1

    return PendingMoveContext(
        base_seq=base_seq,
        base_counts=tuple(base_counts),  # type: ignore[arg-type]
        clicked_col=clicked_col,
        expected_seq=base_seq + str(clicked_col + 1),
        expected_counts=tuple(expected_counts),  # type: ignore[arg-type]
        started_at=started_at,
    )


def mark_retry_attempted(context: PendingMoveContext) -> PendingMoveContext:
    return replace(context, retry_attempted=True)


def evaluate_pending_observation(
    context: PendingMoveContext,
    observation: PendingObservation,
    *,
    auto_commit_timeout_sec: float,
    pending_max_wait_sec: float,
) -> PendingEvaluation:
    """Evaluate one pending-move observation.

    This function is intentionally pure and deterministic so the bridge loop can
    call it each poll tick and map outcomes to side effects (retry click, clear
    pending, logging, etc.).
    """

    obs = observation.counts
    if not _validate_counts(obs):
        return PendingEvaluation(
            state=PendingMoveState.HARD_DIVERGED,
            outcome=PendingMoveOutcome.DIVERGED,
            reason="invalid-observation-counts",
            elapsed_sec=max(0.0, observation.timestamp - context.started_at),
            our_move_proved=False,
            expected_reached=False,
            extra_since_expected=0,
        )

    elapsed = max(0.0, observation.timestamp - context.started_at)

    delta_base = [obs[i] - context.base_counts[i] for i in range(7)]
    if any(d < 0 for d in delta_base):
        return PendingEvaluation(
            state=PendingMoveState.HARD_DIVERGED,
            outcome=PendingMoveOutcome.DIVERGED,
            reason="regression-from-base",
            elapsed_sec=elapsed,
            our_move_proved=False,
            expected_reached=False,
            extra_since_expected=0,
        )

    our_move_proved = delta_base[context.clicked_col] >= 1
    expected_reached = all(obs[i] >= context.expected_counts[i] for i in range(7))
    extra_since_expected = (
        sum(obs[i] - context.expected_counts[i] for i in range(7))
        if expected_reached
        else 0
    )

    if our_move_proved:
        if expected_reached and extra_since_expected == 0:
            reason = "confirmed-exact"
        elif expected_reached and extra_since_expected > 0:
            reason = "confirmed-with-extra-progress"
        else:
            # Our move exists but expected baseline was not fully reached; this can
            # happen on reordered snapshots that still prove click success.
            reason = "confirmed-by-clicked-column-proof"

        return PendingEvaluation(
            state=PendingMoveState.CONFIRMED_PROGRESSING,
            outcome=PendingMoveOutcome.CONFIRMED,
            reason=reason,
            elapsed_sec=elapsed,
            our_move_proved=True,
            expected_reached=expected_reached,
            extra_since_expected=extra_since_expected,
        )

    if elapsed > pending_max_wait_sec:
        return PendingEvaluation(
            state=PendingMoveState.EXPIRED,
            outcome=PendingMoveOutcome.EXPIRED,
            reason="pending-max-wait-exceeded",
            elapsed_sec=elapsed,
            our_move_proved=False,
            expected_reached=False,
            extra_since_expected=0,
        )

    if elapsed > auto_commit_timeout_sec and not context.retry_attempted:
        return PendingEvaluation(
            state=PendingMoveState.RETRY_PENDING,
            outcome=PendingMoveOutcome.RETRY,
            reason="soft-timeout-before-commit-proof",
            elapsed_sec=elapsed,
            our_move_proved=False,
            expected_reached=False,
            extra_since_expected=0,
        )

    return PendingEvaluation(
        state=PendingMoveState.AWAIT_COMMIT,
        outcome=PendingMoveOutcome.WAIT,
        reason="waiting-for-commit-proof",
        elapsed_sec=elapsed,
        our_move_proved=False,
        expected_reached=False,
        extra_since_expected=0,
    )


def state_for_context(context: Optional[PendingMoveContext]) -> PendingMoveState:
    return PendingMoveState.IDLE if context is None else PendingMoveState.AWAIT_COMMIT
