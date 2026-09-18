/** Formatting helpers. Everything numeric renders with tabular figures. */

/** Fixed-decimal number, or an em dash when the value is genuinely unknown.
 *  A missing value must never render as "0.00" — that is a claim, not a blank. */
export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toFixed(digits);
}

/** Signed, for quantities where direction is the point (signal, z, spread). */
export function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const formatted = Math.abs(value).toFixed(digits);
  if (value > 0) return `+${formatted}`;
  if (value < 0) return `−${formatted}`;
  return ` ${formatted}`;
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value.toFixed(digits)}%`;
}

/** "40d" — bars in state. The age of a breakout is half its meaning. */
export function bars(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${value}d`;
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

export function stateLabel(state: string | null | undefined): string {
  if (!state) return '—';
  return state.replace('_', ' ');
}
