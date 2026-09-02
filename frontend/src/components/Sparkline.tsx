interface Props {
  values: Array<number | null>;
  width?: number;
  height?: number;
  stroke?: string;
  label?: string;
}

/** Minimal inline sparkline. No axes, no animation — it exists to show the
 *  direction of travel, nothing more. */
export function Sparkline({
  values,
  width = 120,
  height = 24,
  stroke = '#79828f',
  label,
}: Props) {
  const points = values.filter((v): v is number => v !== null && !Number.isNaN(v));
  if (points.length < 2) {
    return (
      <span className="text-2xs text-term-dim" data-testid="sparkline-empty">
        —
      </span>
    );
  }

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = width / (points.length - 1);

  const path = points
    .map((value, index) => {
      const x = index * step;
      const y = height - ((value - min) / span) * height;
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label ?? 'sparkline'}
      data-testid="sparkline"
    >
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.25} />
    </svg>
  );
}
