import { useMemo, useState } from 'react';
import { scaleLinear } from 'd3-scale';
import { line as d3line } from 'd3-shape';

import { useElementSize } from '../hooks/useElementSize';

import type { RRGResponse, RRGSeries, Quadrant } from '../types/api';
import { num } from '../lib/format';
import { QUADRANT_COLOR, QUADRANT_FILL } from '../lib/signals';

interface Props {
  data: RRGResponse | null;
  loading: boolean;
  dimmed: boolean;
  onSelect: (symbol: string) => void;
  highlighted: string | null;
}

const MARGIN = { top: 16, right: 16, bottom: 32, left: 44 };
const MIN_HALF_SPAN = 2.5;

/**
 * Relative Rotation Graph. The hero panel.
 *
 * TAILS ARE NOT OPTIONAL. A static scatter tells you where a sector sits; the
 * tail tells you which way it is travelling, and the direction is the trade.
 * Healthy rotation traces a counterclockwise loop:
 * Improving -> Leading -> Weakening -> Lagging.
 */
export function RRGChart({ data, loading, dimmed, onSelect, highlighted }: Props) {
  const [hover, setHover] = useState<string | null>(null);
  const [containerRef, size] = useElementSize<HTMLDivElement>();

  const origin = data?.origin ?? 100;

  const { xScale, yScale, innerWidth, innerHeight } = useMemo(() => {
    const innerW = Math.max(200, size.width - MARGIN.left - MARGIN.right);
    const innerH = Math.max(200, size.height - MARGIN.top - MARGIN.bottom);

    const points = (data?.series ?? []).flatMap((s) => s.tail);
    // A symmetric domain centred on the origin keeps the four quadrants equal
    // in area; an auto-fitted domain would make the quadrant a point sits in
    // depend on where the OTHER sectors happen to be.
    const spread = points.length
      ? Math.max(
          MIN_HALF_SPAN,
          ...points.map((p) =>
            Math.max(Math.abs(p.rs_ratio - origin), Math.abs(p.rs_momentum - origin)),
          ),
        ) * 1.12
      : MIN_HALF_SPAN;

    return {
      innerWidth: innerW,
      innerHeight: innerH,
      xScale: scaleLinear().domain([origin - spread, origin + spread]).range([0, innerW]),
      yScale: scaleLinear().domain([origin - spread, origin + spread]).range([innerH, 0]),
    };
  }, [data, origin, size]);

  if (!data) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-term-dim">
        {loading ? 'Loading rotation graph…' : 'No rotation data.'}
      </div>
    );
  }

  const pathFor = d3line<{ rs_ratio: number; rs_momentum: number }>()
    .x((p) => xScale(p.rs_ratio))
    .y((p) => yScale(p.rs_momentum));

  const active = hover ?? highlighted;

  return (
    <section className={`flex h-full min-h-0 flex-col ${dimmed ? 'opacity-40' : ''}`}>
      <div className="flex items-baseline gap-3 border-b border-term-border px-4 py-2">
        <h2 className="text-2xs uppercase tracking-wider text-term-muted">
          Relative rotation
        </h2>
        <span className="text-2xs text-term-dim">
          vs SPY · {data.tail_length}-day tails · rotation reads counterclockwise
        </span>
        {dimmed && (
          <span className="ml-auto text-2xs uppercase tracking-wide text-sig-warn">
            Low dispersion — unreliable
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden" ref={containerRef}>
        <svg
          width={size.width}
          height={size.height}
          role="img"
          aria-label="Relative rotation graph"
          data-testid="rrg-chart"
        >
          <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
            {/* Quadrant fills */}
            {(
              [
                ['LEADING', xScale(origin), 0],
                ['WEAKENING', xScale(origin), yScale(origin)],
                ['LAGGING', 0, yScale(origin)],
                ['IMPROVING', 0, 0],
              ] as Array<[Quadrant, number, number]>
            ).map(([quadrant, x, y]) => (
              <rect
                key={quadrant}
                x={x}
                y={y}
                width={innerWidth / 2}
                height={innerHeight / 2}
                fill={QUADRANT_FILL[quadrant]}
                data-testid={`quadrant-${quadrant}`}
              />
            ))}

            {/* Axes through the origin */}
            <line
              x1={xScale(origin)}
              x2={xScale(origin)}
              y1={0}
              y2={innerHeight}
              stroke="#232935"
            />
            <line
              x1={0}
              x2={innerWidth}
              y1={yScale(origin)}
              y2={yScale(origin)}
              stroke="#232935"
            />

            {/* Quadrant labels */}
            <QuadrantLabel x={innerWidth - 8} y={14} quadrant="LEADING" anchor="end" />
            <QuadrantLabel x={innerWidth - 8} y={innerHeight - 6} quadrant="WEAKENING" anchor="end" />
            <QuadrantLabel x={8} y={innerHeight - 6} quadrant="LAGGING" anchor="start" />
            <QuadrantLabel x={8} y={14} quadrant="IMPROVING" anchor="start" />

            {/* Ticks */}
            {xScale.ticks(6).map((tick) => (
              <text
                key={`x${tick}`}
                x={xScale(tick)}
                y={innerHeight + 16}
                textAnchor="middle"
                className="fill-term-dim font-mono"
                fontSize={9}
              >
                {tick.toFixed(0)}
              </text>
            ))}
            {yScale.ticks(6).map((tick) => (
              <text
                key={`y${tick}`}
                x={-8}
                y={yScale(tick) + 3}
                textAnchor="end"
                className="fill-term-dim font-mono"
                fontSize={9}
              >
                {tick.toFixed(0)}
              </text>
            ))}

            {data.series.map((series) => (
              <SectorTrail
                key={series.symbol}
                series={series}
                path={pathFor(series.tail) ?? ''}
                xScale={xScale}
                yScale={yScale}
                active={active}
                onHover={setHover}
                onSelect={onSelect}
              />
            ))}
          </g>
        </svg>
      </div>
    </section>
  );
}

function QuadrantLabel({
  x,
  y,
  quadrant,
  anchor,
}: {
  x: number;
  y: number;
  quadrant: Quadrant;
  anchor: 'start' | 'end';
}) {
  return (
    <text
      x={x}
      y={y}
      textAnchor={anchor}
      fontSize={9}
      letterSpacing={1}
      fill={QUADRANT_COLOR[quadrant]}
      opacity={0.5}
    >
      {quadrant}
    </text>
  );
}

function SectorTrail({
  series,
  path,
  xScale,
  yScale,
  active,
  onHover,
  onSelect,
}: {
  series: RRGSeries;
  path: string;
  xScale: (v: number) => number;
  yScale: (v: number) => number;
  active: string | null;
  onHover: (symbol: string | null) => void;
  onSelect: (symbol: string) => void;
}) {
  if (series.rs_ratio === null || series.rs_momentum === null || series.tail.length === 0) {
    return null;
  }

  const color = series.quadrant ? QUADRANT_COLOR[series.quadrant] : '#5a6472';
  const isActive = active === series.symbol;
  const faded = active !== null && !isActive;
  const head = series.tail[series.tail.length - 1];

  return (
    <g
      opacity={faded ? 0.22 : 1}
      onMouseEnter={() => onHover(series.symbol)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onSelect(series.symbol)}
      style={{ cursor: 'pointer' }}
      data-testid={`rrg-series-${series.symbol}`}
      data-quadrant={series.quadrant ?? 'NONE'}
    >
      {/* The tail: direction of travel. */}
      <path
        d={path}
        fill="none"
        stroke={color}
        strokeWidth={isActive ? 1.75 : 1}
        strokeOpacity={0.65}
        data-testid={`rrg-tail-${series.symbol}`}
      />
      {/* Tail nodes fade toward the oldest point so direction is readable. */}
      {series.tail.slice(0, -1).map((point, index) => (
        <circle
          key={point.date}
          cx={xScale(point.rs_ratio)}
          cy={yScale(point.rs_momentum)}
          r={1.5}
          fill={color}
          opacity={0.15 + (index / Math.max(1, series.tail.length - 1)) * 0.5}
        />
      ))}
      {/* The head: today. */}
      <circle
        cx={xScale(head.rs_ratio)}
        cy={yScale(head.rs_momentum)}
        r={isActive ? 5.5 : 4}
        fill={color}
        stroke="#0b0d10"
        strokeWidth={1}
      />
      <text
        x={xScale(head.rs_ratio) + 8}
        y={yScale(head.rs_momentum) + 3}
        fontSize={10}
        className="font-mono"
        fill={isActive ? '#d6dae2' : '#79828f'}
      >
        {series.symbol}
      </text>
      {isActive && (
        <title>
          {`${series.symbol} ${series.name}\n${series.quadrant}\nRS-Ratio ${num(
            series.rs_ratio,
          )}  RS-Momentum ${num(series.rs_momentum)}`}
        </title>
      )}
    </g>
  );
}
