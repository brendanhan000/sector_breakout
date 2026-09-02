import { describe, expect, it } from 'vitest';

import {
  QUADRANT_COLOR,
  isConfirmed,
  isDown,
  isFailed,
  isUp,
  readTermShape,
  signalColor,
  stateClasses,
} from '../src/lib/signals';
import type { HorizonPoint } from '../src/types/api';

const term = (s10: number | null, s20: number | null, s55: number | null): HorizonPoint[] => [
  { horizon: 10, signal: s10, c: s10, z_up: null, z_dn: null, channel_max: null, channel_min: null },
  { horizon: 20, signal: s20, c: s20, z_up: null, z_dn: null, channel_max: null, channel_min: null },
  { horizon: 55, signal: s55, c: s55, z_up: null, z_dn: null, channel_max: null, channel_min: null },
];

describe('state predicates', () => {
  it('classifies direction', () => {
    expect(isUp('CONFIRMED_UP')).toBe(true);
    expect(isUp('PENDING_UP')).toBe(true);
    expect(isUp('FAILED_UP')).toBe(true);
    expect(isUp('CONFIRMED_DOWN')).toBe(false);
    expect(isDown('CONFIRMED_DOWN')).toBe(true);
    expect(isDown('NEUTRAL')).toBe(false);
  });

  it('identifies confirmed and failed states', () => {
    expect(isConfirmed('CONFIRMED_UP')).toBe(true);
    expect(isConfirmed('PENDING_UP')).toBe(false);
    expect(isFailed('FAILED_DOWN')).toBe(true);
  });

  it('renders a failed upside breakout in the DOWNSIDE colour', () => {
    // A breakout that confirmed and then reversed is a bearish signal, so it
    // must not stay green just because it started as an upside state.
    expect(stateClasses('FAILED_UP')).toContain('sig-down');
    expect(stateClasses('FAILED_DOWN')).toContain('sig-up');
  });

  it('distinguishes pending from confirmed visually', () => {
    expect(stateClasses('PENDING_UP')).toContain('dashed');
    expect(stateClasses('CONFIRMED_UP')).not.toContain('dashed');
  });
});

describe('readTermShape', () => {
  it('reads an established uptrend when all horizons are positive', () => {
    expect(readTermShape(term(0.8, 0.7, 0.6))).toBe('ESTABLISHED_UP');
  });

  it('reads an established downtrend', () => {
    expect(readTermShape(term(-0.8, -0.7, -0.6))).toBe('ESTABLISHED_DOWN');
  });

  it('reads a pullback inside an uptrend — short negative, long positive', () => {
    // The good entry, and the exact reading a composite average would destroy.
    expect(readTermShape(term(-0.6, 0.1, 0.8))).toBe('PULLBACK_IN_UPTREND');
  });

  it('reads a bounce inside a downtrend', () => {
    expect(readTermShape(term(0.6, -0.1, -0.8))).toBe('BOUNCE_IN_DOWNTREND');
  });

  it('reads extended when every horizon is pinned near the clip', () => {
    expect(readTermShape(term(1.45, 1.4, 1.35))).toBe('EXTENDED');
  });

  it('averaging would collapse distinct shapes to the same number', () => {
    // Both of these average to 0.1, yet they are opposite readings. This is the
    // whole argument for storing the term structure instead of a composite.
    const pullback = term(-0.6, 0.1, 0.8);
    const bounce = term(0.8, 0.1, -0.6);

    const mean = (t: HorizonPoint[]) =>
      t.reduce((sum, p) => sum + (p.signal ?? 0), 0) / t.length;

    expect(mean(pullback)).toBeCloseTo(mean(bounce), 10);
    expect(readTermShape(pullback)).not.toBe(readTermShape(bounce));
  });

  it('handles missing horizons without throwing', () => {
    expect(readTermShape(term(null, null, null))).toBe('MIXED');
  });
});

describe('signalColor', () => {
  it('maps sign to colour with a neutral band', () => {
    expect(signalColor(0.9)).toBe('#2ec27e');
    expect(signalColor(-0.9)).toBe('#e5484d');
    expect(signalColor(0.1)).toBe('#79828f');
    expect(signalColor(null)).toBe('#4c5563');
  });
});

describe('quadrant colours', () => {
  it('matches the specified mapping', () => {
    expect(QUADRANT_COLOR.LEADING).toBe('#2ec27e');   // green
    expect(QUADRANT_COLOR.WEAKENING).toBe('#e2a03f'); // yellow
    expect(QUADRANT_COLOR.LAGGING).toBe('#e5484d');   // red
    expect(QUADRANT_COLOR.IMPROVING).toBe('#3d8bfd'); // blue
  });
});
