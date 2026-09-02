/**
 * Shared visual semantics.
 *
 * Colour carries meaning and nothing else in this UI, so the mapping from a
 * state or quadrant to a colour lives in exactly one place. A green dot in the
 * RRG and a green badge in the state grid must mean the same thing.
 */

import type { HorizonPoint, Quadrant, SectorState } from '../types/api';

export const QUADRANT_COLOR: Record<Quadrant, string> = {
  LEADING: '#2ec27e',   // green
  WEAKENING: '#e2a03f', // yellow
  LAGGING: '#e5484d',   // red
  IMPROVING: '#3d8bfd', // blue
};

export const QUADRANT_FILL: Record<Quadrant, string> = {
  LEADING: 'rgba(46,194,126,0.05)',
  WEAKENING: 'rgba(226,160,63,0.05)',
  LAGGING: 'rgba(229,72,77,0.05)',
  IMPROVING: 'rgba(61,139,253,0.05)',
};

export function isUp(state: SectorState | null | undefined): boolean {
  return state === 'PENDING_UP' || state === 'CONFIRMED_UP' || state === 'FAILED_UP';
}

export function isDown(state: SectorState | null | undefined): boolean {
  return state === 'PENDING_DOWN' || state === 'CONFIRMED_DOWN' || state === 'FAILED_DOWN';
}

/**
 * Directional reading: +1 bullish, -1 bearish, 0 no opinion.
 *
 * Mirrors `State.bias` in backend/engine/state.py. Distinct from isUp/isDown,
 * which describe which breakout FAMILY a state belongs to. They differ on the
 * failure states: FAILED_UP began as an upside breakout but reads BEARISH,
 * because a breakout that confirmed and then reversed is the highest-quality
 * downside signal the system produces.
 */
export function bias(state: SectorState | null | undefined): number {
  switch (state) {
    case 'PENDING_UP':
    case 'CONFIRMED_UP':
    case 'FAILED_DOWN':
      return 1;
    case 'PENDING_DOWN':
    case 'CONFIRMED_DOWN':
    case 'FAILED_UP':
      return -1;
    default:
      return 0;
  }
}

/** True when the two planes point in opposite directions — the actionable rows. */
export function planesDisagree(
  absolute: SectorState | null | undefined,
  relative: SectorState | null | undefined,
): boolean {
  return bias(absolute) * bias(relative) < 0;
}

export function isConfirmed(state: SectorState | null | undefined): boolean {
  return state === 'CONFIRMED_UP' || state === 'CONFIRMED_DOWN';
}

export function isFailed(state: SectorState | null | undefined): boolean {
  return state === 'FAILED_UP' || state === 'FAILED_DOWN';
}

/** Tailwind classes for a state badge. Confirmed states are filled; pending
 *  states are outlined; failed states are struck through in the opposite
 *  colour, because a failed upside breakout is a DOWNSIDE signal. */
export function stateClasses(state: SectorState | null | undefined): string {
  switch (state) {
    case 'CONFIRMED_UP':
      return 'bg-sig-up/20 text-sig-up border-sig-up/60';
    case 'PENDING_UP':
      return 'bg-transparent text-sig-up/80 border-sig-up/35 border-dashed';
    case 'FAILED_UP':
      return 'bg-sig-down/15 text-sig-down border-sig-down/50 line-through decoration-1';
    case 'CONFIRMED_DOWN':
      return 'bg-sig-down/20 text-sig-down border-sig-down/60';
    case 'PENDING_DOWN':
      return 'bg-transparent text-sig-down/80 border-sig-down/35 border-dashed';
    case 'FAILED_DOWN':
      return 'bg-sig-up/15 text-sig-up border-sig-up/50 line-through decoration-1';
    default:
      return 'bg-transparent text-term-muted border-term-border';
  }
}

/** Colour for a signal value in [-clip, +clip]. */
export function signalColor(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '#4c5563';
  if (value > 0.3) return '#2ec27e';
  if (value < -0.3) return '#e5484d';
  return '#79828f';
}

export type TermShape =
  | 'ESTABLISHED_UP'
  | 'ESTABLISHED_DOWN'
  | 'EARLY_REVERSAL'
  | 'PULLBACK_IN_UPTREND'
  | 'BOUNCE_IN_DOWNTREND'
  | 'EXTENDED'
  | 'MIXED';

/**
 * Read the SHAPE of the term structure.
 *
 * This is why the three horizons are never averaged into a composite: the
 * shape distinguishes an established trend from a pullback inside one, and a
 * mean would collapse both to the same number.
 */
export function readTermShape(term: HorizonPoint[], clip = 1.5): TermShape {
  const sorted = [...term].sort((a, b) => a.horizon - b.horizon);
  const values = sorted.map((t) => t.signal).filter((v): v is number => v !== null);
  if (values.length < 2) return 'MIXED';

  const short = values[0];
  const long = values[values.length - 1];
  const extended = values.every((v) => Math.abs(v) > clip * 0.85);

  if (extended) return 'EXTENDED';
  if (values.every((v) => v > 0.3)) return 'ESTABLISHED_UP';
  if (values.every((v) => v < -0.3)) return 'ESTABLISHED_DOWN';
  if (short < -0.3 && long > 0.3) return 'PULLBACK_IN_UPTREND';
  if (short > 0.3 && long < -0.3) return 'BOUNCE_IN_DOWNTREND';
  if (Math.sign(short) !== Math.sign(long)) return 'EARLY_REVERSAL';
  return 'MIXED';
}

export const TERM_SHAPE_LABEL: Record<TermShape, string> = {
  ESTABLISHED_UP: 'Established uptrend',
  ESTABLISHED_DOWN: 'Established downtrend',
  EARLY_REVERSAL: 'Early reversal',
  PULLBACK_IN_UPTREND: 'Pullback in uptrend',
  BOUNCE_IN_DOWNTREND: 'Bounce in downtrend',
  EXTENDED: 'Extended — expect mean reversion',
  MIXED: 'Mixed',
};
