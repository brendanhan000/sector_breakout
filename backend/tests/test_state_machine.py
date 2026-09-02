"""State machine invariants, property-based.

Hypothesis generates arbitrary (and adversarial) signal / price / volume paths.
The machine must never violate its own contract regardless of what it is fed —
including NaN, exact-threshold values, and violent sign flips.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import array_shapes, arrays

from backend.engine.params import StateParams
from backend.engine.state import LEGAL_TRANSITIONS, State, run_state_machine, transition_history

SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

PARAMS = StateParams(
    pending_entry=0.90,
    pending_exit=0.50,
    confirmed_exit=0.30,
    confirm_consecutive_closes=2,
    confirm_rvol=1.3,
    fail_window_bars=5,
    failed_cooldown_bars=10,
)

finite = st.floats(min_value=-2.0, max_value=2.0, allow_nan=False, allow_infinity=False)
prices = st.floats(min_value=1.0, max_value=500.0, allow_nan=False, allow_infinity=False)
volumes = st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False)


def _series(values, name):
    idx = pd.bdate_range("2015-01-02", periods=len(values))
    return pd.Series(np.asarray(values, dtype="float64"), index=idx, name=name)


def _run(signal, close, max_n, min_n, rvol, params=PARAMS):
    n = len(signal)
    idx = pd.bdate_range("2015-01-02", periods=n)
    to = lambda v: pd.Series(np.asarray(v, dtype="float64"), index=idx)
    return run_state_machine(to(signal), to(close), to(max_n), to(min_n), to(rvol), params)


@st.composite
def paths(draw, min_len: int = 30, max_len: int = 250):
    n = draw(st.integers(min_value=min_len, max_value=max_len))
    signal = draw(arrays(np.float64, n, elements=finite))
    close = draw(arrays(np.float64, n, elements=prices))
    # Channel edges derived from close so the geometry is not nonsense.
    spread = draw(st.floats(min_value=0.1, max_value=20.0))
    offset = draw(arrays(np.float64, n, elements=st.floats(-10.0, 10.0, allow_nan=False)))
    max_n = close + spread + offset
    min_n = close - spread + offset
    rvol = draw(arrays(np.float64, n, elements=volumes))
    return signal, close, max_n, min_n, rvol


@given(paths())
@SETTINGS
def test_every_transition_is_legal(path):
    machine = _run(*path)
    states = [s for s in machine["state"] if s is not None]
    for a, b in zip(states, states[1:]):
        if a != b:
            assert (State(a), State(b)) in LEGAL_TRANSITIONS, f"illegal edge {a} -> {b}"


@given(paths())
@SETTINGS
def test_no_state_is_ever_skipped(path):
    """CONFIRMED is reachable only from PENDING; FAILED only from CONFIRMED.

    The machine cannot leap from NEUTRAL straight to CONFIRMED_UP, which is what
    guarantees the two-consecutive-close persistence requirement is actually
    enforced rather than merely documented.
    """
    machine = _run(*path)
    states = [s for s in machine["state"] if s is not None]
    for a, b in zip(states, states[1:]):
        if a == b:
            continue
        a, b = State(a), State(b)
        if b is State.CONFIRMED_UP:
            assert a is State.PENDING_UP
        if b is State.CONFIRMED_DOWN:
            assert a is State.PENDING_DOWN
        if b is State.FAILED_UP:
            assert a is State.CONFIRMED_UP
        if b is State.FAILED_DOWN:
            assert a is State.CONFIRMED_DOWN
        if b is State.PENDING_UP or b is State.PENDING_DOWN:
            assert a is State.NEUTRAL


@given(paths())
@SETTINGS
def test_bars_in_state_increases_monotonically_within_a_state(path):
    """Age is 0 on the entry bar and increases by exactly 1 thereafter."""
    machine = _run(*path)
    prev_state = None
    prev_age = None

    for state, age, entered in zip(
        machine["state"], machine["bars_in_state"], machine["entered"]
    ):
        if state is None:
            assert pd.isna(age)
            continue
        age = int(age)
        if prev_state is None or entered:
            assert age == 0, "a newly entered state must start at age 0"
        else:
            assert state == prev_state, "state changed without entered=True"
            assert age == prev_age + 1, f"age jumped {prev_age} -> {age} within a state"
        prev_state, prev_age = state, age


@given(paths())
@SETTINGS
def test_state_changes_exactly_when_entered_is_true(path):
    machine = _run(*path)
    rows = [
        (s, bool(e))
        for s, e in zip(machine["state"], machine["entered"])
        if s is not None
    ]
    for (prev_s, _), (cur_s, cur_entered) in zip(rows, rows[1:]):
        assert (prev_s != cur_s) == cur_entered, (
            f"entered flag disagrees with the state change {prev_s} -> {cur_s}"
        )


@given(paths())
@SETTINGS
def test_at_most_one_transition_per_bar(path):
    """No oscillation within a single bar.

    Structurally guaranteed by evaluating one edge per bar, asserted here
    because it is the property that keeps the machine from resolving a violent
    reversal into an invented multi-step history on a single date.
    """
    machine = _run(*path)
    assert len(machine) == len(machine.index.unique())
    states = machine["state"].tolist()
    assert len(states) == len(machine)


@given(paths())
@SETTINGS
def test_confirmation_always_satisfies_both_gates(path):
    """Every CONFIRMED_* entry bar must show N consecutive closes AND volume."""
    signal, close, max_n, min_n, rvol = path
    machine = _run(*path)

    for i, (state, entered) in enumerate(zip(machine["state"], machine["entered"])):
        if not entered or state is None:
            continue
        s = State(state)
        if s is State.CONFIRMED_UP:
            assert machine["consec_above"].iloc[i] >= PARAMS.confirm_consecutive_closes
            assert rvol[i] > PARAMS.confirm_rvol
        if s is State.CONFIRMED_DOWN:
            assert machine["consec_below"].iloc[i] >= PARAMS.confirm_consecutive_closes
            assert rvol[i] > PARAMS.confirm_rvol


@given(paths())
@SETTINGS
def test_hysteresis_holds(path):
    """Entry and exit thresholds are asymmetric, and the gap must be respected.

    A PENDING_UP state is only abandoned once the signal falls below
    pending_exit; a CONFIRMED_UP only below confirmed_exit. Without this gap the
    machine chatters at the boundary and the grid becomes unreadable.
    """
    signal, *_ = path
    machine = _run(*path)
    states = list(machine["state"])

    for i in range(1, len(states)):
        prev, cur = states[i - 1], states[i]
        if prev is None or cur is None or prev == cur:
            continue
        prev, cur = State(prev), State(cur)
        if prev is State.PENDING_UP and cur is State.NEUTRAL:
            assert signal[i] < PARAMS.pending_exit
        if prev is State.PENDING_DOWN and cur is State.NEUTRAL:
            assert signal[i] > -PARAMS.pending_exit
        if prev is State.CONFIRMED_UP and cur is State.NEUTRAL:
            assert signal[i] < PARAMS.confirmed_exit
        if prev is State.CONFIRMED_DOWN and cur is State.NEUTRAL:
            assert signal[i] > -PARAMS.confirmed_exit


@given(paths())
@SETTINGS
def test_pending_requires_crossing_the_entry_threshold(path):
    signal, *_ = path
    machine = _run(*path)
    states = list(machine["state"])
    for i in range(len(states)):
        if states[i] is None or not machine["entered"].iloc[i]:
            continue
        s = State(states[i])
        if s is State.PENDING_UP:
            assert signal[i] > PARAMS.pending_entry
        if s is State.PENDING_DOWN:
            assert signal[i] < -PARAMS.pending_entry


@given(paths())
@SETTINGS
def test_failed_states_respect_the_failure_window(path):
    """FAILED_* may only be entered within fail_window_bars of confirmation."""
    machine = _run(*path)
    states = list(machine["state"])
    ages = list(machine["bars_in_state"])

    for i in range(1, len(states)):
        if states[i] is None or states[i - 1] is None:
            continue
        prev, cur = State(states[i - 1]), State(states[i])
        if cur in (State.FAILED_UP, State.FAILED_DOWN) and prev is not cur:
            # Age carried into this bar is the previous bar's age + 1.
            age_at_bar = int(ages[i - 1]) + 1
            assert age_at_bar <= PARAMS.fail_window_bars


@given(paths())
@SETTINGS
def test_failed_cooldown_is_exact(path):
    """FAILED_* is terminal for exactly failed_cooldown_bars, then NEUTRAL."""
    machine = _run(*path)
    states = list(machine["state"])
    ages = list(machine["bars_in_state"])

    for i in range(1, len(states)):
        if states[i] is None or states[i - 1] is None:
            continue
        prev, cur = State(states[i - 1]), State(states[i])
        if prev in (State.FAILED_UP, State.FAILED_DOWN):
            if cur is State.NEUTRAL:
                assert int(ages[i - 1]) + 1 >= PARAMS.failed_cooldown_bars
            else:
                assert cur is prev
                assert int(ages[i]) < PARAMS.failed_cooldown_bars


@given(paths())
@SETTINGS
def test_machine_is_deterministic(path):
    a = _run(*path)
    b = _run(*path)
    assert a["state"].tolist() == b["state"].tolist()
    assert a["bars_in_state"].fillna(-1).tolist() == b["bars_in_state"].fillna(-1).tolist()


# --------------------------------------------------------------------------- #
# Targeted, non-property cases
# --------------------------------------------------------------------------- #
def test_nan_signal_holds_the_current_state():
    """A missing signal is not information; the machine must not act on it."""
    n = 20
    signal = np.full(n, 0.95)
    signal[10:13] = np.nan
    close = np.full(n, 100.0)
    machine = _run(signal, close, np.full(n, 99.0), np.full(n, 90.0), np.full(n, 1.0))
    assert State(machine["state"].iloc[9]) is State.PENDING_UP
    assert State(machine["state"].iloc[12]) is State.PENDING_UP
    assert not machine["entered"].iloc[10:13].any()


def test_all_nan_signal_produces_no_states():
    n = 20
    machine = _run(
        np.full(n, np.nan), np.full(n, 100.0), np.full(n, 99.0), np.full(n, 90.0),
        np.full(n, 1.0),
    )
    assert machine["state"].isna().all()
    assert machine["bars_in_state"].isna().all()


def test_close_exactly_at_the_level_is_not_a_breakout():
    """Strict inequality: touching the prior high has not broken it.

    Equality here is the difference between a real breakout and a level being
    tested, and treating them the same is the most common way a Donchian system
    accumulates false positives.
    """
    n = 30
    machine = _run(
        np.full(n, 0.95), np.full(n, 100.0), np.full(n, 100.0), np.full(n, 90.0),
        np.full(n, 2.0),
    )
    assert (machine["consec_above"] == 0).all()
    assert State.CONFIRMED_UP not in {State(s) for s in machine["state"] if s is not None}


def test_a_single_close_beyond_the_level_does_not_confirm():
    """Persistence: one close through is not two."""
    n = 30
    close = np.full(n, 100.0)
    close[15] = 105.0  # one bar only
    machine = _run(np.full(n, 0.95), close, np.full(n, 101.0), np.full(n, 90.0), np.full(n, 2.0))
    assert machine["consec_above"].max() == 1
    assert State.CONFIRMED_UP not in {State(s) for s in machine["state"] if s is not None}


def test_two_consecutive_closes_with_volume_confirm():
    n = 30
    close = np.full(n, 100.0)
    close[15:17] = 105.0
    machine = _run(np.full(n, 0.95), close, np.full(n, 101.0), np.full(n, 90.0), np.full(n, 2.0))
    entries = [(i, State(s)) for i, (s, e) in enumerate(zip(machine["state"], machine["entered"])) if e]
    assert (16, State.CONFIRMED_UP) in entries


def test_two_consecutive_closes_without_volume_do_not_confirm():
    n = 30
    close = np.full(n, 100.0)
    close[15:17] = 105.0
    machine = _run(np.full(n, 0.95), close, np.full(n, 101.0), np.full(n, 90.0), np.full(n, 1.0))
    assert State.CONFIRMED_UP not in {State(s) for s in machine["state"] if s is not None}


def test_confirmed_up_that_reverses_becomes_failed_up():
    n = 40
    signal = np.full(n, 0.95)
    close = np.full(n, 100.0)
    close[15:17] = 105.0
    signal[18:] = -0.4          # collapse through zero, 2 bars after confirmation
    machine = _run(signal, close, np.full(n, 101.0), np.full(n, 90.0), np.full(n, 2.0))
    states = [State(s) for s in machine["state"] if s is not None]
    assert State.FAILED_UP in states


def test_confirmed_up_that_fades_slowly_goes_neutral_not_failed():
    """Beyond the failure window a decaying signal is a fade, not a failure.

    The distinction matters: FAILED_* is the system's highest-quality reversal
    signal, and diluting it with ordinary trend exhaustion would destroy that.
    """
    n = 60
    signal = np.full(n, 0.95)
    close = np.full(n, 100.0)
    close[15:17] = 105.0
    signal[17:24] = 0.85        # hold through the 5-bar failure window
    signal[24:] = -0.5          # only then collapse
    machine = _run(signal, close, np.full(n, 101.0), np.full(n, 90.0), np.full(n, 2.0))
    states = [State(s) for s in machine["state"] if s is not None]
    assert State.FAILED_UP not in states
    assert State.CONFIRMED_UP in states


def test_failed_state_clears_after_exactly_the_cooldown():
    n = 60
    signal = np.full(n, 0.95)
    close = np.full(n, 100.0)
    close[15:17] = 105.0
    signal[18:] = -0.4
    machine = _run(signal, close, np.full(n, 101.0), np.full(n, 90.0), np.full(n, 2.0))

    entries = [(i, State(s)) for i, (s, e) in enumerate(zip(machine["state"], machine["entered"])) if e]
    failed_at = next(i for i, s in entries if s is State.FAILED_UP)
    cleared_at = next(i for i, s in entries if s is State.NEUTRAL and i > failed_at)
    assert cleared_at - failed_at == PARAMS.failed_cooldown_bars


def test_transition_history_records_from_and_to():
    n = 40
    signal = np.full(n, 0.95)
    close = np.full(n, 100.0)
    close[15:17] = 105.0
    machine = _run(signal, close, np.full(n, 101.0), np.full(n, 90.0), np.full(n, 2.0))
    history = transition_history(machine)

    assert list(history.columns) == ["from_state", "to_state", "bars_in_state"]
    assert len(history) >= 2
    confirm_rows = history[history["to_state"] == State.CONFIRMED_UP]
    assert len(confirm_rows) == 1
    assert State(confirm_rows["from_state"].iloc[0]) is State.PENDING_UP


# --------------------------------------------------------------------------- #
# Directional bias
# --------------------------------------------------------------------------- #
def test_bias_treats_a_failed_upside_breakout_as_bearish():
    """FAILED_UP belongs to the upside FAMILY but reads BEARISH.

    A breakout that confirmed and then reversed is the highest-quality downside
    signal the system produces. Anything comparing two states directionally —
    the plane disagreement flag above all — must use bias, not is_up/is_down.
    """
    assert State.FAILED_UP.is_up is True       # family membership
    assert State.FAILED_UP.bias == -1          # directional reading
    assert State.FAILED_DOWN.is_down is True
    assert State.FAILED_DOWN.bias == 1


def test_bias_of_every_state():
    assert State.PENDING_UP.bias == 1
    assert State.CONFIRMED_UP.bias == 1
    assert State.PENDING_DOWN.bias == -1
    assert State.CONFIRMED_DOWN.bias == -1
    assert State.NEUTRAL.bias == 0


def test_breaking_out_excludes_failed_states():
    """The breadth filter asks 'is this sector breaking out right now?'.
    A failed breakout is over, so it must not carry a NARROW badge."""
    assert State.PENDING_UP.is_breaking_out
    assert State.CONFIRMED_DOWN.is_breaking_out
    assert not State.FAILED_UP.is_breaking_out
    assert not State.NEUTRAL.is_breaking_out
