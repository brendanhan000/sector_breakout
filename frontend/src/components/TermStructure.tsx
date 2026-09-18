import type { HorizonPoint } from '../types/api';
import { num } from '../lib/format';
import { TERM_SHAPE_LABEL, readTermShape, signalColor } from '../lib/signals';

interface Props {
  term: HorizonPoint[];
}

// Signal magnitude beyond this is rendered as fully saturated.
const CLIP = 1.5;

/**
 * The three horizons as a compact three-dot indicator, short to long.
 *
 * Rendered side by side and NEVER averaged. The shape is the diagnostic:
 * all three positive is an established trend; short negative with long positive
 * is a pullback inside an uptrend (the good entry); all three pinned is
 * extended. A composite number erases every one of those distinctions.
 */
export function TermStructure({ term }: Props) {
  const sorted = [...term].sort((a, b) => a.horizon - b.horizon);
  const shape = readTermShape(sorted, CLIP);

  return (
    <span
      className="inline-flex items-center gap-1"
      title={`${TERM_SHAPE_LABEL[shape]} — ${sorted
        .map((t) => `${t.horizon}d ${num(t.signal)}`)
        .join('  ')}`}
      data-testid="term-structure"
      data-shape={shape}
    >
      {sorted.map((point) => {
        const value = point.signal ?? 0;
        const magnitude = Math.min(Math.abs(value) / CLIP, 1);
        return (
          <span
            key={point.horizon}
            className="relative inline-flex h-4 w-4 items-center justify-center"
            data-testid={`term-dot-${point.horizon}`}
          >
            <span
              className="block rounded-full"
              style={{
                width: `${5 + magnitude * 5}px`,
                height: `${5 + magnitude * 5}px`,
                backgroundColor: signalColor(point.signal),
                opacity: point.signal === null ? 0.25 : 0.45 + magnitude * 0.55,
              }}
            />
          </span>
        );
      })}
    </span>
  );
}
