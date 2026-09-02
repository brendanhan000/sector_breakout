import { describe, expect, it } from 'vitest';

import { bars, num, pct, shortDate, signed, stateLabel } from '../src/lib/format';

describe('num', () => {
  it('formats to fixed decimals', () => {
    expect(num(1.2345)).toBe('1.23');
    expect(num(1.2345, 4)).toBe('1.2345');
  });

  it('renders missing values as an em dash, never as zero', () => {
    // "0.00" is a claim about the data. A blank is the truth.
    expect(num(null)).toBe('—');
    expect(num(undefined)).toBe('—');
    expect(num(NaN)).toBe('—');
  });
});

describe('signed', () => {
  it('always shows direction', () => {
    expect(signed(1.5)).toBe('+1.50');
    expect(signed(-1.5)).toBe('−1.50');
    expect(signed(0)).toBe(' 0.00');
  });

  it('handles missing values', () => {
    expect(signed(null)).toBe('—');
  });
});

describe('bars', () => {
  it('formats the age of a state', () => {
    expect(bars(40)).toBe('40d');
    expect(bars(0)).toBe('0d');
    expect(bars(null)).toBe('—');
  });
});

describe('pct and dates', () => {
  it('formats percentiles', () => {
    expect(pct(62.4)).toBe('62%');
    expect(pct(null)).toBe('—');
  });

  it('formats dates', () => {
    expect(shortDate('2024-06-28')).toBe('2024-06-28');
    expect(shortDate(null)).toBe('—');
  });

  it('humanises state labels', () => {
    expect(stateLabel('CONFIRMED_UP')).toBe('CONFIRMED UP');
    expect(stateLabel(null)).toBe('—');
  });
});
