import type { ReactNode } from 'react';

import type { RegimeResponse } from '../types/api';
import { num, pct, shortDate } from '../lib/format';
import { Sparkline } from './Sparkline';
import { StateBadge } from './StateBadge';

interface Props {
  regime: RegimeResponse | null;
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}

const QUALITY_STYLE: Record<string, string> = {
  OK: 'text-sig-up border-sig-up/40',
  WARMING_UP: 'text-sig-info border-sig-info/40',
  DEGRADED: 'text-sig-warn border-sig-warn/40',
  STALE: 'text-sig-down border-sig-down/40',
};

/** Full-width, always visible. SPY state, dispersion, correlation, as_of. */
export function RegimeHeader({ regime, loading, refreshing, onRefresh }: Props) {
  return (
    <header className="border-b border-term-border bg-term-panel">
      <div className="flex items-stretch gap-0 px-4">
        <Cell label="Benchmark" width="w-56">
          {regime ? (
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm text-term-text">{regime.benchmark.symbol}</span>
              <StateBadge
                state={regime.benchmark.state}
                barsInState={regime.benchmark.bars_in_state}
              />
            </div>
          ) : (
            <Placeholder loading={loading} />
          )}
        </Cell>

        <Cell label="Dispersion" width="w-72">
          {regime ? <DispersionGauge regime={regime} /> : <Placeholder loading={loading} />}
        </Cell>

        <Cell label="Mean correlation" width="w-64">
          {regime ? (
            <div className="flex items-center gap-3">
              <span className="font-mono text-sm tabular-nums text-term-text">
                {num(regime.correlation)}
              </span>
              <Sparkline
                values={regime.correlation_sparkline.map((p) => p.value)}
                label="Mean pairwise sector correlation"
                stroke="#79828f"
              />
            </div>
          ) : (
            <Placeholder loading={loading} />
          )}
        </Cell>

        <Cell label="As of" width="w-44">
          {regime ? (
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm tabular-nums text-term-text">
                {shortDate(regime.as_of)}
              </span>
              <span
                className={`rounded-sm border px-1.5 py-0.5 text-2xs uppercase tracking-wide ${
                  QUALITY_STYLE[regime.data_quality] ?? 'text-term-muted border-term-border'
                }`}
                title="Data quality of the latest fully-computed day"
              >
                {regime.data_quality}
              </span>
            </div>
          ) : (
            <Placeholder loading={loading} />
          )}
        </Cell>

        <div className="ml-auto flex items-center pl-4">
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="rounded-sm border border-term-border px-3 py-1 text-2xs uppercase tracking-wide text-term-muted transition-colors hover:border-term-muted hover:text-term-text disabled:opacity-40"
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {regime?.low_dispersion && (
        <div
          className="border-t border-sig-warn/30 bg-sig-warn/10 px-4 py-1.5 text-2xs uppercase tracking-wide text-sig-warn"
          data-testid="low-dispersion-banner"
          role="status"
        >
          {regime.message ?? 'Low dispersion — rotation signals unreliable.'}
          <span className="ml-2 normal-case tracking-normal text-sig-warn/70">
            Dispersion is in the {pct(regime.dispersion_percentile)} percentile of its own
            3-year history; every relative-plane signal below is noise.
          </span>
        </div>
      )}
    </header>
  );
}

function DispersionGauge({ regime }: { regime: RegimeResponse }) {
  const percentile = regime.dispersion_percentile;
  const filled = percentile ?? 0;
  const low = regime.low_dispersion;

  return (
    <div className="flex items-center gap-3">
      <span className="font-mono text-sm tabular-nums text-term-text">
        {percentile === null ? '—' : pct(percentile)}
      </span>
      <div
        className="relative h-2 w-32 overflow-hidden rounded-sm bg-term-grid"
        title={`Dispersion ${num(regime.dispersion, 4)} — ${pct(percentile)} of trailing 3y`}
      >
        <div
          className={low ? 'h-full bg-sig-warn' : 'h-full bg-sig-info'}
          style={{ width: `${Math.max(1, Math.min(100, filled))}%` }}
          data-testid="dispersion-fill"
        />
        {/* The quartile gate that grays out the relative plane. */}
        <div
          className="absolute top-0 h-full w-px bg-term-muted"
          style={{ left: `${regime.low_dispersion_threshold}%` }}
        />
      </div>
    </div>
  );
}

function Cell({
  label,
  width,
  children,
}: {
  label: string;
  width: string;
  children: ReactNode;
}) {
  return (
    <div className={`${width} border-r border-term-border px-3 py-2`}>
      <div className="mb-1 text-2xs uppercase tracking-wider text-term-dim">{label}</div>
      {children}
    </div>
  );
}

function Placeholder({ loading }: { loading: boolean }) {
  return <span className="text-sm text-term-dim">{loading ? '…' : '—'}</span>;
}
