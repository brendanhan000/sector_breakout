"""Per-sector, per-plane breakout state machine (runs at the configured horizon).

    NEUTRAL      -> PENDING_UP     when signal > +entry
    PENDING_UP   -> CONFIRMED_UP   when Nth consecutive close beyond max_N AND RVOL > gate
    PENDING_UP   -> NEUTRAL        when signal < +pending_exit
    CONFIRMED_UP -> NEUTRAL        when signal < +confirmed_exit
    CONFIRMED_UP -> FAILED_UP      when signal < 0 within `fail_window_bars` of confirmation
    FAILED_UP    -> NEUTRAL        after `failed_cooldown_bars`
    (mirrored for the downside)

Two design decisions that are load-bearing:

1. ASYMMETRIC ENTRY/EXIT. Entry at 0.90, exit at 0.30. With symmetric thresholds
   a signal loitering at the boundary flips state on nearly every bar and the
   dashboard becomes unreadable noise. The gap is hysteresis, and it is not
   optional.

2. CLOSES ONLY, NEVER INTRADAY TOUCHES, AND NEVER FEWER THAN N OF THEM.
   Confirmation requires consecutive *closes* beyond the level. A single wick
   through a Donchian high is the most common false positive in trend following.

FAILED_* is a real terminal state with persisted history, not an error code. A
breakout that confirms and then immediately reverses is the highest-quality
reversal signal the system produces, so it gets a name and a record.

Every threshold arrives via StateParams (built from config.yaml). There are no
numeric literals in the transition logic below.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

from .params import StateParams

__all__ = ["State", "LEGAL_TRANSITIONS", "run_state_machine", "transition_history"]


class State(StrEnum):
    NEUTRAL = "NEUTRAL"
    PENDING_UP = "PENDING_UP"
    CONFIRMED_UP = "CONFIRMED_UP"
    FAILED_UP = "FAILED_UP"
    PENDING_DOWN = "PENDING_DOWN"
    CONFIRMED_DOWN = "CONFIRMED_DOWN"
    FAILED_DOWN = "FAILED_DOWN"

    @property
    def is_up(self) -> bool:
        return self in (State.PENDING_UP, State.CONFIRMED_UP, State.FAILED_UP)

    @property
    def is_down(self) -> bool:
        return self in (State.PENDING_DOWN, State.CONFIRMED_DOWN, State.FAILED_DOWN)

    @property
    def is_confirmed(self) -> bool:
        return self in (State.CONFIRMED_UP, State.CONFIRMED_DOWN)

    @property
    def bias(self) -> int:
        """Directional reading: +1 bullish, -1 bearish, 0 no opinion.

        Distinct from ``is_up`` / ``is_down``, which describe which breakout
        FAMILY a state belongs to. The two differ precisely on the failure
        states: FAILED_UP belongs to the upside family (it began as an upside
        breakout) but reads BEARISH, because an upside breakout that confirmed
        and then reversed is the highest-quality downside signal this system
        produces. Anything comparing two states directionally — the plane
        disagreement flag above all — must use this, not is_up/is_down.
        """
        if self in (State.PENDING_UP, State.CONFIRMED_UP, State.FAILED_DOWN):
            return 1
        if self in (State.PENDING_DOWN, State.CONFIRMED_DOWN, State.FAILED_UP):
            return -1
        return 0

    @property
    def is_breaking_out(self) -> bool:
        """Pending or confirmed in either direction — used by the breadth filter."""
        return self in (
            State.PENDING_UP,
            State.CONFIRMED_UP,
            State.PENDING_DOWN,
            State.CONFIRMED_DOWN,
        )


#: The complete set of edges the machine may traverse. Anything not in here is a
#: bug, and the machine raises rather than emitting an impossible history.
LEGAL_TRANSITIONS: frozenset[tuple[State, State]] = frozenset(
    {
        (State.NEUTRAL, State.PENDING_UP),
        (State.NEUTRAL, State.PENDING_DOWN),
        (State.PENDING_UP, State.CONFIRMED_UP),
        (State.PENDING_UP, State.NEUTRAL),
        (State.CONFIRMED_UP, State.NEUTRAL),
        (State.CONFIRMED_UP, State.FAILED_UP),
        (State.FAILED_UP, State.NEUTRAL),
        (State.PENDING_DOWN, State.CONFIRMED_DOWN),
        (State.PENDING_DOWN, State.NEUTRAL),
        (State.CONFIRMED_DOWN, State.NEUTRAL),
        (State.CONFIRMED_DOWN, State.FAILED_DOWN),
        (State.FAILED_DOWN, State.NEUTRAL),
    }
)


def _consecutive_true(flags: np.ndarray) -> np.ndarray:
    """Run length of consecutive True values, reset to 0 on every False."""
    out = np.zeros(flags.shape[0], dtype="int64")
    run = 0
    for i, f in enumerate(flags):
        run = run + 1 if f else 0
        out[i] = run
    return out


def _next_state(
    current: State,
    age: int,
    signal: float,
    consec_above: int,
    consec_below: int,
    rvol: float,
    p: StateParams,
) -> State:
    """Evaluate at most ONE transition for this bar.

    Returning at most one edge per bar is what guarantees the machine can never
    oscillate within a single bar or skip an intermediate state (a crash cannot
    jump PENDING_UP -> PENDING_DOWN; it routes through NEUTRAL on the next bar).
    """
    if np.isnan(signal):
        return current

    volume_confirms = (not np.isnan(rvol)) and rvol > p.confirm_rvol

    if current is State.NEUTRAL:
        if signal > p.pending_entry:
            return State.PENDING_UP
        if signal < -p.pending_entry:
            return State.PENDING_DOWN
        return current

    if current is State.PENDING_UP:
        if consec_above >= p.confirm_consecutive_closes and volume_confirms:
            return State.CONFIRMED_UP
        if signal < p.pending_exit:
            return State.NEUTRAL
        return current

    if current is State.PENDING_DOWN:
        if consec_below >= p.confirm_consecutive_closes and volume_confirms:
            return State.CONFIRMED_DOWN
        if signal > -p.pending_exit:
            return State.NEUTRAL
        return current

    if current is State.CONFIRMED_UP:
        # Failure is checked first: signal < 0 also satisfies signal <
        # confirmed_exit, and the distinction between "faded" and "reversed
        # outright" only survives if the stricter test wins.
        if signal < 0.0 and age <= p.fail_window_bars:
            return State.FAILED_UP
        if signal < p.confirmed_exit:
            return State.NEUTRAL
        return current

    if current is State.CONFIRMED_DOWN:
        if signal > 0.0 and age <= p.fail_window_bars:
            return State.FAILED_DOWN
        if signal > -p.confirmed_exit:
            return State.NEUTRAL
        return current

    if current in (State.FAILED_UP, State.FAILED_DOWN):
        if age >= p.failed_cooldown_bars:
            return State.NEUTRAL
        return current

    raise AssertionError(f"unhandled state {current!r}")


def run_state_machine(
    signal: pd.Series,
    close: pd.Series,
    max_n: pd.Series,
    min_n: pd.Series,
    rvol: pd.Series,
    params: StateParams,
) -> pd.DataFrame:
    """Run the machine bar by bar.

    Returns a frame indexed like ``signal`` with columns:
        state             the State on that bar
        bars_in_state     age in bars; 0 on the bar the state was entered
        consec_above      consecutive closes strictly above max_N
        consec_below      consecutive closes strictly below min_N
        entered           True on a bar where the state changed

    Bars before the first non-NaN signal are left empty — the machine has no
    opinion until it has a signal.
    """
    idx = signal.index
    n = len(idx)

    sig = signal.to_numpy(dtype="float64")
    cls = close.reindex(idx).to_numpy(dtype="float64")
    mx = max_n.reindex(idx).to_numpy(dtype="float64")
    mn = min_n.reindex(idx).to_numpy(dtype="float64")
    rv = rvol.reindex(idx).to_numpy(dtype="float64")

    # Strict inequality: a close exactly *at* the prior high has not broken it.
    above = np.where(np.isnan(mx) | np.isnan(cls), False, cls > mx)
    below = np.where(np.isnan(mn) | np.isnan(cls), False, cls < mn)
    consec_above = _consecutive_true(above)
    consec_below = _consecutive_true(below)

    states: list[State | None] = [None] * n
    ages = np.full(n, -1, dtype="int64")
    entered = np.zeros(n, dtype=bool)

    valid = np.flatnonzero(~np.isnan(sig))
    if valid.size == 0:
        return pd.DataFrame(
            {
                "state": pd.Series([None] * n, index=idx, dtype="object"),
                "bars_in_state": pd.Series(pd.NA, index=idx, dtype="Int64"),
                "consec_above": pd.Series(consec_above, index=idx),
                "consec_below": pd.Series(consec_below, index=idx),
                "entered": pd.Series(entered, index=idx),
            }
        )

    start = int(valid[0])
    prev_state = State.NEUTRAL
    prev_age = -1

    for t in range(start, n):
        age_now = prev_age + 1
        new_state = _next_state(
            prev_state,
            age_now,
            sig[t],
            int(consec_above[t]),
            int(consec_below[t]),
            rv[t],
            params,
        )

        if new_state is not prev_state:
            if (prev_state, new_state) not in LEGAL_TRANSITIONS:
                raise AssertionError(
                    f"illegal transition {prev_state} -> {new_state} at {idx[t]!r}"
                )
            states[t] = new_state
            ages[t] = 0
            entered[t] = True
        else:
            states[t] = prev_state
            ages[t] = age_now

        prev_state = states[t]
        prev_age = int(ages[t])

    age_series = pd.Series(ages, index=idx, dtype="int64").astype("Int64")
    age_series[age_series < 0] = pd.NA

    return pd.DataFrame(
        {
            "state": pd.Series(states, index=idx, dtype="object"),
            "bars_in_state": age_series,
            "consec_above": pd.Series(consec_above, index=idx),
            "consec_below": pd.Series(consec_below, index=idx),
            "entered": pd.Series(entered, index=idx),
        }
    )


def transition_history(machine: pd.DataFrame) -> pd.DataFrame:
    """Extract just the bars where the state changed, with the previous state.

    This is what the detail drawer renders, and it is how FAILED_* events stay
    visible long after the machine has cooled back to NEUTRAL.
    """
    changed = machine.loc[machine["entered"].fillna(False).astype(bool)].copy()
    prior = machine["state"].shift(1)
    changed["from_state"] = prior.reindex(changed.index)
    changed = changed.rename(columns={"state": "to_state"})
    return changed[["from_state", "to_state", "bars_in_state"]]
