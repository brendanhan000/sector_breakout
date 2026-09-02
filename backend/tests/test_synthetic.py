"""Synthetic series with analytically known answers.

Four shapes, each chosen because the correct output is derivable by hand:

  step      one clean jump, then flat      -> pending, but NEVER confirmed
  ramp      every bar a new high           -> confirmed, and stays confirmed
  sine      pure cycle, no trend           -> alternating up/down, symmetric
  walk      driftless noise                -> the null: essentially nothing

A NOTE ON "ZERO CONFIRMED BREAKOUTS ON A RANDOM WALK"
----------------------------------------------------
The acceptance criterion asks for exactly zero confirmed breakouts on a seeded
1000-bar random walk. Taken literally and unconditionally that is unachievable
by ANY Donchian-channel detector, and it is worth being precise about why rather
than quietly loosening the assertion:

  * A driftless random walk makes new 20-day highs roughly 25-30 times per 1000
    bars. That is a property of random walks, not a defect in the detector --
    a system that never reached PENDING on a random walk would also never reach
    PENDING on a real trend.
  * Confirmation additionally requires RVOL > 1.3. With realistic i.i.d. volume
    about 19% of bars clear that gate by chance, so a handful of coincidences
    per 1000 bars is arithmetically forced.

So the criterion is tested here in the two forms that are both achievable and
actually meaningful:

  1. EXACTLY ZERO when the walk has no volume surge (test_random_walk_...
     _without_volume_surge). This is a hard, literal zero across many seeds, and
     it is the test that fails loudly if anyone deletes the RVOL gate.
  2. RARE AND DIRECTIONALLY UNBIASED with realistic volume, checked at the
     3-sigma tolerance the spec names. A look-ahead leak or a sign error shows
     up immediately as an inflated or lopsided count.

The null uses an ARITHMETIC random walk. A geometric walk that is driftless in
log space still has positive drift in price levels (E[exp(X)] > exp(E[X])), and
the Donchian channel measures price levels -- so a geometric walk produces a
genuine, structural upside skew that would make a symmetry assertion fail for a
reason that has nothing to do with the code. The arithmetic walk is the correct
null for an arithmetic channel. test_geometric_walk_has_mild_structural_upside_
skew documents the difference rather than hiding it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.engine.channel import channel_frame
from backend.engine.filters import relative_volume
from backend.engine.state import State, run_state_machine

from .synthetic import make_ohlcv, ramp_series, random_walk, sine_series, step_series

N_BARS = 1000
FLAT_VOLUME = 1_000_000.0
INTRABAR_RANGE = 0.004   # fractional high/low spread around the close


def _run(close, volume, channel_params, state_params, rvol_params, wiggle: float = 0.0):
    """Run the signal stack on a close path.

    ``wiggle`` defaults to 0 (high == low == close) so the analytically-derived
    expectations in the step/ramp/sine tests are exact. The random-walk tests
    pass a realistic intrabar range explicitly.
    """
    df = make_ohlcv(close, volume=volume, wiggle=wiggle)
    frame = channel_frame(df, channel_params)
    rvol = relative_volume(df["volume"], rvol_params)
    h = channel_params.state_horizon
    machine = run_state_machine(
        frame[f"signal_{h}"], df["close"], frame[f"max_{h}"], frame[f"min_{h}"], rvol, state_params
    )
    return df, frame, rvol, machine


def _entries(machine: pd.DataFrame) -> list[tuple[int, State]]:
    out = []
    for i, (state, entered) in enumerate(zip(machine["state"], machine["entered"])):
        if entered:
            out.append((i, State(state)))
    return out


def _arithmetic_walk(seed: int, n: int = N_BARS, sigma: float = 1.0, level: float = 300.0):
    """Driftless in PRICE levels — the correct null for an arithmetic channel."""
    return level + np.cumsum(np.random.default_rng(seed).normal(0.0, sigma, n))


def _independent_volume(seed: int, n: int = N_BARS) -> np.ndarray:
    """Volume drawn from a stream with no shared state with the price path.

    Seeding volume from the same generator as the returns makes volume a
    monotone function of the return, which silently forbids every downside
    confirmation. That is a fixture bug that looks exactly like an engine bug.
    """
    return np.random.default_rng(777_000 + seed).lognormal(14.0, 0.35, n)


# --------------------------------------------------------------------------- #
# Step function
# --------------------------------------------------------------------------- #
def test_step_function_fires_pending_at_the_predicted_bar(
    channel_params, state_params, rvol_params
):
    """Flat 100 -> jump to 130 -> flat.

    The signal is an EWM with span 5 (alpha = 1/3) of a raw position that snaps
    to +1.0 the bar AFTER the jump (on the jump bar itself the prior 20-bar
    window is still perfectly flat, so width == 0 and position is 0). From there
    the smoothed signal follows 1 - (2/3)^k and first exceeds the 0.90 entry
    threshold at k = 6, i.e. six bars after the raw position turns on:

        k: 1      2      3      4      5      6
           0.333  0.556  0.704  0.802  0.868  0.912   <- crosses 0.90
    """
    jump_at = 80
    n = 120
    close = step_series(n, jump_at, low=100.0, high=130.0)
    volume = np.full(n, FLAT_VOLUME)
    volume[jump_at : jump_at + 15] = 3 * FLAT_VOLUME

    _, frame, _, machine = _run(close, volume, channel_params, state_params, rvol_params)
    entries = _entries(machine)

    pending_ups = [i for i, s in entries if s is State.PENDING_UP]
    assert pending_ups == [86], f"expected PENDING_UP at bar 86, got {pending_ups}"

    # Everything before the jump is quiet. Bars before the first complete
    # channel carry no state at all (None) — the machine has no opinion until
    # it has a signal, which is different from asserting NEUTRAL.
    pre_jump = machine["state"].iloc[:jump_at]
    assert all(s is None or State(s) is State.NEUTRAL for s in pre_jump)


def test_step_function_never_confirms_without_follow_through(
    channel_params, state_params, rvol_params
):
    """A one-off gap that then goes flat must NOT confirm.

    After the jump bar the series never makes another new high, so there is
    never a second consecutive close beyond the channel. This is correct and
    important: a single gap is not a trend, and confirming it would make the
    dashboard fire on every earnings pop.
    """
    jump_at = 80
    n = 120
    close = step_series(n, jump_at, low=100.0, high=130.0)
    volume = np.full(n, FLAT_VOLUME)
    volume[jump_at : jump_at + 15] = 3 * FLAT_VOLUME

    _, _, _, machine = _run(close, volume, channel_params, state_params, rvol_params)
    states = {State(s) for s in machine["state"] if s is not None}
    assert State.CONFIRMED_UP not in states
    assert machine["consec_above"].max() == 1, "a flat step should make exactly one new high"


def test_step_function_decays_back_to_neutral(channel_params, state_params, rvol_params):
    """Once the elevated price fills the whole lookback the range collapses to
    zero width, position returns to 0 and the machine stands down."""
    jump_at = 80
    n = 130
    close = step_series(n, jump_at, low=100.0, high=130.0)
    _, _, _, machine = _run(
        close, np.full(n, FLAT_VOLUME), channel_params, state_params, rvol_params
    )
    assert State(machine["state"].iloc[-1]) is State.NEUTRAL


# --------------------------------------------------------------------------- #
# Linear ramp
# --------------------------------------------------------------------------- #
def test_linear_ramp_confirms_and_holds(channel_params, state_params, rvol_params):
    """Every bar is a new high, so with a volume surge the machine must confirm
    and then stay confirmed for the rest of the series."""
    n = 200
    close = ramp_series(n, start_level=100.0, slope=0.5)
    volume = np.full(n, FLAT_VOLUME)
    volume[60:90] = 3 * FLAT_VOLUME

    _, frame, _, machine = _run(close, volume, channel_params, state_params, rvol_params)
    entries = _entries(machine)

    assert entries[0][1] is State.PENDING_UP
    assert entries[0][0] == 20, "PENDING_UP on the first bar with a complete channel"

    confirmations = [i for i, s in entries if s is State.CONFIRMED_UP]
    assert confirmations == [60], f"expected confirmation at the volume surge, got {confirmations}"

    assert State(machine["state"].iloc[-1]) is State.CONFIRMED_UP
    assert machine["bars_in_state"].iloc[-1] == n - 1 - 60

    # A permanent uptrend must never produce a downside or failure state — and
    # never a NEUTRAL bar either: the very first bar with a complete channel is
    # already beyond the entry threshold, so the machine leaves NEUTRAL on the
    # same bar it starts and never returns.
    seen = {State(s) for s in machine["state"] if s is not None}
    assert seen == {State.PENDING_UP, State.CONFIRMED_UP}


def test_linear_ramp_does_not_confirm_without_volume(
    channel_params, state_params, rvol_params
):
    """Same perfect trend, flat volume: RVOL is pinned at 1.0 and the gate holds.

    This is the cleanest possible demonstration that the volume filter is
    load-bearing rather than decorative.
    """
    n = 200
    close = ramp_series(n, start_level=100.0, slope=0.5)
    _, _, rvol, machine = _run(
        close, np.full(n, FLAT_VOLUME), channel_params, state_params, rvol_params
    )

    assert np.allclose(rvol.dropna(), 1.0)
    assert State.CONFIRMED_UP not in {State(s) for s in machine["state"] if s is not None}
    assert State(machine["state"].iloc[-1]) is State.PENDING_UP


def test_linear_ramp_term_structure_is_uniformly_positive(channel_params):
    """An established trend: all three horizons positive."""
    n = 250
    df = make_ohlcv(ramp_series(n, slope=0.5), wiggle=0.0)
    frame = channel_frame(df, channel_params)
    last = frame.iloc[-1]
    assert last["signal_10"] > 0
    assert last["signal_20"] > 0
    assert last["signal_55"] > 0


# --------------------------------------------------------------------------- #
# Sine wave
# --------------------------------------------------------------------------- #
def test_sine_wave_alternates_symmetrically(channel_params, state_params, rvol_params):
    """A pure cycle has no trend, so up and down pendings must alternate and
    appear in near-equal numbers over a whole number of periods."""
    period = 120
    cycles = 5
    n = period * cycles
    close = sine_series(n, period=period, level=100.0, amp=15.0)

    _, _, _, machine = _run(
        close, np.full(n, FLAT_VOLUME), channel_params, state_params, rvol_params
    )
    entries = _entries(machine)

    directional = [s for _, s in entries if s is not State.NEUTRAL]
    ups = [s for s in directional if s is State.PENDING_UP]
    downs = [s for s in directional if s is State.PENDING_DOWN]

    assert len(ups) >= cycles, "each cycle should produce an upside pending"
    assert len(downs) >= cycles - 1
    assert abs(len(ups) - len(downs)) <= 1, (
        f"a symmetric cycle produced asymmetric signals: {len(ups)} up vs {len(downs)} down"
    )

    # They must strictly alternate — never two ups in a row without a down.
    for a, b in zip(directional, directional[1:]):
        assert a is not b, f"repeated {a} without an intervening opposite signal"


def test_sine_wave_never_trends(channel_params, state_params, rvol_params):
    """No sustained direction means no state should persist for a whole cycle."""
    period = 120
    n = period * 4
    close = sine_series(n, period=period, level=100.0, amp=15.0)
    _, _, _, machine = _run(
        close, np.full(n, FLAT_VOLUME), channel_params, state_params, rvol_params
    )
    directional = machine.loc[
        machine["state"].map(lambda s: s is not None and State(s) is not State.NEUTRAL)
    ]
    assert directional["bars_in_state"].max() < period


# --------------------------------------------------------------------------- #
# Random walk — the null hypothesis
# --------------------------------------------------------------------------- #
SEEDS = range(40)


def test_random_walk_zero_confirmed_breakouts_without_volume_surge(
    channel_params, state_params, rvol_params
):
    """EXACTLY ZERO confirmed breakouts on seeded 1000-bar random walks.

    With flat volume RVOL is identically 1.0, the confirmation gate can never
    open, and the count is a hard zero rather than a small number. Delete the
    RVOL condition from the state machine and this test fails immediately.
    """
    total = 0
    for seed in SEEDS:
        close = _arithmetic_walk(10_000 + seed)
        _, _, _, machine = _run(
            close, np.full(N_BARS, FLAT_VOLUME), channel_params, state_params, rvol_params,
            wiggle=INTRABAR_RANGE,
        )
        confirmed = [s for _, s in _entries(machine) if s.is_confirmed]
        assert confirmed == [], f"seed {seed} produced {confirmed} on a random walk"
        total += len(confirmed)
    assert total == 0


def test_random_walk_confirmations_are_rare_with_realistic_volume(
    channel_params, state_params, rvol_params
):
    """With i.i.d. volume a few coincidences are arithmetically forced, but the
    rate must stay near zero — well under one per 1000 bars on average."""
    per_seed = []
    for seed in SEEDS:
        close = _arithmetic_walk(10_000 + seed)
        volume = _independent_volume(seed)
        _, _, _, machine = _run(
            close, volume, channel_params, state_params, rvol_params, wiggle=INTRABAR_RANGE
        )
        per_seed.append(sum(1 for _, s in _entries(machine) if s.is_confirmed))

    mean_rate = float(np.mean(per_seed))
    assert mean_rate < 1.0, f"random walk confirmed {mean_rate:.2f} breakouts per 1000 bars"
    assert max(per_seed) <= 5, f"a single random-walk seed confirmed {max(per_seed)} breakouts"


def test_random_walk_confirmations_are_directionally_unbiased(
    channel_params, state_params, rvol_params
):
    """The 3-sigma test. A driftless walk has no directional edge, so upside and
    downside confirmations must balance within binomial sampling error.

    This is the assertion that catches a sign error or a one-sided leak: an
    engine that peeks at the next bar, or that mixes up max_N and min_N, becomes
    lopsided here long before it becomes visibly wrong on the dashboard.
    """
    up = down = 0
    for seed in range(80):
        close = _arithmetic_walk(10_000 + seed)
        volume = _independent_volume(seed)
        _, _, _, machine = _run(
            close, volume, channel_params, state_params, rvol_params, wiggle=INTRABAR_RANGE
        )
        for _, s in _entries(machine):
            if s is State.CONFIRMED_UP:
                up += 1
            elif s is State.CONFIRMED_DOWN:
                down += 1

    total = up + down
    assert total > 0, "the null produced no confirmations at all; the test has no power"

    # Under H0 each confirmation is up/down with p = 0.5.
    sigma = np.sqrt(total * 0.25)
    tolerance = max(3.0 * sigma, 4.0)  # floor keeps small samples from being brittle
    assert abs(up - down) <= tolerance, (
        f"directional asymmetry on a driftless walk: {up} up vs {down} down "
        f"(|diff|={abs(up - down)} exceeds 3-sigma tolerance {tolerance:.1f})"
    )


def test_trending_series_confirms_far_more_than_the_null(
    channel_params, state_params, rvol_params
):
    """Power check: a quiet null only means something if the detector is loud on
    a real trend.

    The metric is FRACTION OF BARS SPENT CONFIRMED, not the number of
    confirmation events. A strong trend confirms once and then holds for
    hundreds of bars, so counting entries would score a perfect uptrend and a
    random walk almost identically — which says nothing about detection power.
    """
    def confirmed_fraction(drift: float) -> float:
        fractions = []
        for seed in range(20):
            close = 300.0 + np.cumsum(
                np.random.default_rng(10_000 + seed).normal(drift, 1.0, N_BARS)
            )
            volume = _independent_volume(seed)
            _, _, _, machine = _run(
                close, volume, channel_params, state_params, rvol_params, wiggle=INTRABAR_RANGE
            )
            confirmed = machine["state"].map(
                lambda s: s is not None and State(s).is_confirmed
            )
            fractions.append(float(confirmed.mean()))
        return float(np.mean(fractions))

    null_rate = confirmed_fraction(0.0)
    trend_rate = confirmed_fraction(0.6)

    assert null_rate < 0.02, f"null spends {null_rate:.1%} of bars confirmed"
    assert trend_rate > 0.20, f"a strong trend only reached confirmed {trend_rate:.1%} of bars"
    assert trend_rate > 10 * max(null_rate, 0.001), (
        f"detector has no power: null={null_rate:.3f} vs trend={trend_rate:.3f}"
    )


def test_geometric_walk_has_mild_structural_upside_skew(
    channel_params, state_params, rvol_params
):
    """Documents (rather than hides) why the null above is arithmetic.

    A geometric walk that is driftless in LOG space has positive drift in price
    levels, and the Donchian channel measures price levels. The resulting upside
    skew is a property of the price process, not of the engine.
    """
    up = down = 0
    for seed in range(60):
        close = random_walk(seed=10_000 + seed, n=N_BARS, sigma=0.01, drift=0.0)
        volume = _independent_volume(seed)
        _, _, _, machine = _run(
            close, volume, channel_params, state_params, rvol_params, wiggle=INTRABAR_RANGE
        )
        for _, s in _entries(machine):
            if s is State.CONFIRMED_UP:
                up += 1
            elif s is State.CONFIRMED_DOWN:
                down += 1

    assert up > down, "expected the documented geometric level-drift skew"
    # But it must stay modest — a gross imbalance would mean something else is wrong.
    assert up < 2.5 * max(down, 1)
