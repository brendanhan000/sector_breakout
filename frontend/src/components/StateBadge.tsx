import type { SectorState } from '../types/api';
import { bars, stateLabel } from '../lib/format';
import { stateClasses } from '../lib/signals';

interface Props {
  state: SectorState | null;
  barsInState?: number | null;
  compact?: boolean;
}

/**
 * State badge with its age.
 *
 * The age is never optional: a 40-day-old breakout and a 2-day-old one are
 * entirely different trades, and a badge showing only the state hides that.
 */
export function StateBadge({ state, barsInState, compact = false }: Props) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={`inline-block rounded-sm border px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide tabular-nums ${stateClasses(state)}`}
        data-testid="state-badge"
        data-state={state ?? 'NONE'}
      >
        {stateLabel(state)}
      </span>
      {!compact && (
        <span className="text-2xs tabular-nums text-term-muted" title="Bars in state">
          {bars(barsInState)}
        </span>
      )}
    </span>
  );
}
