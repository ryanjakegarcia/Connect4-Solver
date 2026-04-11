# Pending Move Confirmation State Machine (Design)

## Goal

Eliminate false "pending move diverged" resets when the board legally advances faster than one poll cycle, without weakening board integrity checks.

This state machine targets the pending-move flow currently handled in the bridge loop after a bot click.

## Problem Summary

Current behavior can produce false divergence when:

1. We click our move.
2. The site commits our move.
3. Opponent replies quickly before next poll.
4. Observed board no longer equals `expected` (`base + our_move`) and no longer equals `base`.

The existing logic treats this as divergence instead of legal progression.

## Core Principle

Use strict token-count proofs and move-origin proofs instead of exact sequence-only equality.

- Keep strict checks for regressions and impossible transitions.
- Accept fast legal progression if it still proves our move committed.

## Inputs (per poll)

Use one atomic snapshot from parser:

- `seq`: parsed move sequence string
- `counts[7]`: per-column heights from same parse pass
- `ts`: poll timestamp

Pending context captured at click time:

- `base_seq`
- `base_counts[7]`
- `clicked_col` (0-based)
- `expected_seq = base_seq + str(clicked_col + 1)`
- `expected_counts = base_counts` with `clicked_col += 1`
- `started_at`
- `retry_attempted`

## Derived Metrics

Given `obs_counts` from current snapshot:

- `delta_base[i] = obs_counts[i] - base_counts[i]`
- `delta_expected[i] = obs_counts[i] - expected_counts[i]`
- `sum_base = sum(delta_base)`
- `sum_expected = sum(delta_expected)`

Strict validity helpers:

- `has_regression`: any `delta_base[i] < 0`
- `has_illegal_growth`: any `obs_counts[i] > 6`
- `our_move_proved`: `delta_base[clicked_col] >= 1`
- `expected_reached`: all `obs_counts[i] >= expected_counts[i]`
- `extra_since_expected = sum_expected` (only meaningful if `expected_reached`)

## State Definitions

### S0: IDLE
No pending click in flight.

### S1: AWAIT_COMMIT
We clicked; waiting for proof our move committed.

### S2: CONFIRMED_PROGRESSING
Our move is proven committed; board may already include additional legal moves.

### S3: RETRY_PENDING
Soft timeout reached before commit proof; one retry click may be attempted.

### S4: HARD_DIVERGED
Observed transition is impossible/conflicting; clear pending and re-evaluate.

### S5: EXPIRED
Pending window exceeded; release pending lock and re-evaluate.

## Transitions

### S0 -> S1 (on click sent)
Set pending context fields.

### S1 -> S2 (commit proven)
Guard:

- no regression
- no illegal growth
- `our_move_proved == true`

Action:

- clear pending immediately (or mark committed and keep lightweight post-confirm window)
- do not log divergence

### S1 -> S4 (hard divergence)
Guard (any):

- regression from base (`has_regression`)
- impossible growth (`has_illegal_growth`)
- clicked column cannot contain our move while other columns advanced in conflicting way beyond policy

Action:

- clear pending
- log hard divergence once
- optionally set short blocked cooldown

### S1 -> S3 (soft timeout)
Guard:

- elapsed > `AUTO_COMMIT_TIMEOUT_SEC`
- `our_move_proved == false`
- `retry_attempted == false`

Action:

- attempt single retry click in `clicked_col` if column not full
- set `retry_attempted = true`

### S3 -> S2 (commit proven after retry)
Same proof guard as S1 -> S2.

### S3 -> S4 (hard divergence after retry)
Same hard divergence guard as S1 -> S4.

### S1/S3 -> S5 (pending expired)
Guard:

- elapsed > `PENDING_MAX_WAIT_SEC`

Action:

- clear pending
- apply short blocked cooldown based on base position
- log timeout once

### S2 -> S0
Immediate if pending is cleared at confirmation.

## Legal Fast-Progression Policy

This policy is strict but non-fragile:

Treat as confirmed (not diverged) when all are true:

1. `our_move_proved == true`
2. no regression
3. no illegal growth

Then classify additional moves:

- `extra_since_expected == 0`: exact expected state
- `extra_since_expected == 1`: opponent moved quickly; legal progression
- `extra_since_expected >= 2`: still legal progression if no regressions and counts valid, but log at debug level for telemetry

No divergence warning should be emitted in these cases.

## Logging Policy

Emit exactly one operator-facing message per pending cycle for anomalies:

- hard divergence: one warning
- timeout: one warning
- fast legal progression: debug/info only (no warning spam)

Recommended events:

- `pending_confirmed_exact`
- `pending_confirmed_with_extra`
- `pending_hard_diverged`
- `pending_retry_sent`
- `pending_expired`

## Integration Points

Primary integration area:

- `ui/browser_bridge.py` pending block around existing "pending expected/base" checks.

Required parser contract:

- sequence and counts should come from same parse pass (atomic snapshot).

Helpers to add/replace:

- `build_counts(seq)` (if counts not available)
- `derive_pending_observation(base_counts, expected_counts, obs_counts, clicked_col)`
- `evaluate_pending_state(...) -> (next_state, actions)`

## Pseudocode (implementation sketch)

```python
if pending is None:
    state = S0
else:
    obs = snapshot()  # seq + counts from same poll
    checks = evaluate(obs, pending)

    if checks.hard_diverged:
        state = S4
        clear_pending()
        log_once("pending_hard_diverged")

    elif checks.our_move_proved:
        state = S2
        clear_pending()
        if checks.extra_since_expected > 0:
            debug("pending_confirmed_with_extra")

    elif elapsed > AUTO_COMMIT_TIMEOUT_SEC and not pending.retry_attempted:
        state = S3
        retry_if_column_open()

    elif elapsed > PENDING_MAX_WAIT_SEC:
        state = S5
        clear_pending_with_cooldown()
        log_once("pending_expired")

    else:
        state = S1
        continue_waiting()
```

## Test Scenarios

1. Exact commit
- base -> expected, no extra move.
- Expected: confirm, no warning.

2. Fast opponent reply
- base -> expected+1 before next poll.
- Expected: confirm with extra, no divergence warning.

3. Slow UI commit then retry success
- no commit by soft timeout, retry succeeds.
- Expected: one retry log, then confirm.

4. True divergence/regression
- counts decrease or impossible transition.
- Expected: hard divergence once.

5. Pending timeout
- no proof of commit within max window.
- Expected: timeout once, clear pending.

## Non-goals

- This design does not change solver move selection.
- This design does not weaken legality checks.
- This design does not add manual fallback behavior in auto mode.
