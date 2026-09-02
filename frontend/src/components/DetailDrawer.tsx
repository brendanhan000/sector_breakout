import { useState, type ReactNode } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { HistoryResponse, Plane } from '../types/api';
import { bars, num, shortDate, signed, stateLabel } from '../lib/format';
import { isFailed, stateClasses } from '../lib/signals';

interface Props {
  symbol: string | null;
  history: HistoryResponse | null;
  loading: boolean;
  plane: Plane;
  onPlaneChange: (plane: Plane) => void;
  onClose: () => void;
}

const AXIS = { stroke: '#4c5563', fontSize: 10 };
const GRID = '#1b2029';

export function DetailDrawer({
  symbol,
  history,
  loading,
  plane,
  onPlaneChange,
  onClose,
}: Props) {
  const [showChannels, setShowChannels] = useState(true);

  if (!symbol) return null;

  return (
    <aside
      className="fixed inset-y-0 right-0 z-30 flex w-[46rem] flex-col border-l border-term-border bg-term-panel shadow-2xl"
      data-testid="detail-drawer"
    >
      <div className="flex items-center gap-3 border-b border-term-border px-4 py-2">
        <span className="font-mono text-sm text-term-text">{symbol}</span>
        <span className="text-2xs text-term-muted">{history?.name}</span>

        <div className="ml-auto flex items-center gap-1">
          {(['absolute', 'relative'] as Plane[]).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => onPlaneChange(option)}
              className={`rounded-sm border px-2 py-0.5 text-2xs uppercase tracking-wide transition-colors ${
                plane === option
                  ? 'border-term-muted text-term-text'
                  : 'border-term-border text-term-dim hover:text-term-muted'
              }`}
            >
              {option}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setShowChannels((v) => !v)}
            className="ml-2 rounded-sm border border-term-border px-2 py-0.5 text-2xs uppercase tracking-wide text-term-dim hover:text-term-muted"
          >
            {showChannels ? 'hide channels' : 'show channels'}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close detail"
            className="ml-2 rounded-sm border border-term-border px-2 py-0.5 text-2xs text-term-dim hover:text-term-text"
          >
            ✕
          </button>
        </div>
      </div>

      {loading && <div className="px-4 py-6 text-sm text-term-dim">Loading history…</div>}

      {history && (
        <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
          <Panel title={`Price with ${plane === 'relative' ? 'residual' : 'Donchian'} channels`}>
            <PriceChart history={history} plane={plane} showChannels={showChannels} />
          </Panel>

          <Panel title="Residual series (beta-adjusted vs SPY)">
            <ResidualChart history={history} />
          </Panel>

          <Panel title="Signal term structure">
            <SignalChart history={history} />
          </Panel>

          <Panel title="State transitions">
            <TransitionTable history={history} />
          </Panel>
        </div>
      )}
    </aside>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-5">
      <h3 className="mb-1.5 text-2xs uppercase tracking-wider text-term-dim">{title}</h3>
      {children}
    </section>
  );
}

function toRows(history: HistoryResponse) {
  return history.series.map((point) => {
    const row: Record<string, number | string | null> = {
      date: point.date,
      close: history.plane === 'relative' ? point.residual : point.close,
      residual: point.residual,
      rvol: point.rvol,
    };
    for (const [horizon, values] of Object.entries(point.term)) {
      row[`signal_${horizon}`] = values.signal ?? null;
      row[`max_${horizon}`] = values.max ?? null;
      row[`min_${horizon}`] = values.min ?? null;
    }
    return row;
  });
}

const CHANNEL_STROKE: Record<string, string> = {
  '10': '#3d8bfd',
  '20': '#e2a03f',
  '55': '#8b5cf6',
};

function PriceChart({
  history,
  plane,
  showChannels,
}: {
  history: HistoryResponse;
  plane: Plane;
  showChannels: boolean;
}) {
  const rows = toRows(history);
  const horizons = Object.keys(history.series[0]?.term ?? {}).sort(
    (a, b) => Number(a) - Number(b),
  );

  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="date" tick={AXIS} minTickGap={48} tickLine={false} />
          <YAxis tick={AXIS} width={52} domain={['auto', 'auto']} tickLine={false} />
          <Tooltip content={<DarkTooltip />} />
          <Line
            type="linear"
            dataKey="close"
            stroke="#d6dae2"
            strokeWidth={1.25}
            dot={false}
            isAnimationActive={false}
            name={plane === 'relative' ? 'residual' : 'close'}
          />
          {showChannels &&
            horizons.flatMap((h) => [
              <Line
                key={`max${h}`}
                type="stepAfter"
                dataKey={`max_${h}`}
                stroke={CHANNEL_STROKE[h] ?? '#5a6472'}
                strokeWidth={0.75}
                strokeOpacity={0.55}
                dot={false}
                isAnimationActive={false}
                name={`${h}d high`}
              />,
              <Line
                key={`min${h}`}
                type="stepAfter"
                dataKey={`min_${h}`}
                stroke={CHANNEL_STROKE[h] ?? '#5a6472'}
                strokeWidth={0.75}
                strokeOpacity={0.55}
                dot={false}
                isAnimationActive={false}
                name={`${h}d low`}
              />,
            ])}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ResidualChart({ history }: { history: HistoryResponse }) {
  const rows = toRows(history);
  return (
    <div className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="date" tick={AXIS} minTickGap={48} tickLine={false} />
          <YAxis tick={AXIS} width={52} domain={['auto', 'auto']} tickLine={false} />
          <Tooltip content={<DarkTooltip />} />
          <ReferenceLine y={100} stroke="#232935" />
          <Line
            type="linear"
            dataKey="residual"
            stroke="#3d8bfd"
            strokeWidth={1.25}
            dot={false}
            isAnimationActive={false}
            name="residual"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function SignalChart({ history }: { history: HistoryResponse }) {
  const rows = toRows(history);
  const horizons = Object.keys(history.series[0]?.term ?? {}).sort(
    (a, b) => Number(a) - Number(b),
  );

  return (
    <div className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="date" tick={AXIS} minTickGap={48} tickLine={false} />
          <YAxis tick={AXIS} width={52} domain={[-1.6, 1.6]} tickLine={false} />
          <Tooltip content={<DarkTooltip />} />
          {/* Entry and exit thresholds — the hysteresis band. */}
          <ReferenceLine y={0.9} stroke="#2ec27e" strokeOpacity={0.35} strokeDasharray="3 3" />
          <ReferenceLine y={0.3} stroke="#2ec27e" strokeOpacity={0.2} />
          <ReferenceLine y={0} stroke="#232935" />
          <ReferenceLine y={-0.3} stroke="#e5484d" strokeOpacity={0.2} />
          <ReferenceLine y={-0.9} stroke="#e5484d" strokeOpacity={0.35} strokeDasharray="3 3" />
          {horizons.map((h) => (
            <Line
              key={h}
              type="linear"
              dataKey={`signal_${h}`}
              stroke={CHANNEL_STROKE[h] ?? '#5a6472'}
              strokeWidth={1.1}
              dot={false}
              isAnimationActive={false}
              name={`${h}d`}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Transition history, newest first. FAILED_* rows are called out: a breakout
 *  that confirmed and then reversed is the highest-quality reversal signal the
 *  system produces, and it must stay visible long after the state has cooled. */
function TransitionTable({ history }: { history: HistoryResponse }) {
  const rows = [...history.transitions].reverse();

  if (rows.length === 0) {
    return <p className="text-2xs text-term-dim">No transitions in range.</p>;
  }

  return (
    <table className="w-full border-collapse text-2xs">
      <thead>
        <tr className="text-term-dim">
          <th className="border-b border-term-border py-1 text-left font-normal">Date</th>
          <th className="border-b border-term-border py-1 text-left font-normal">From</th>
          <th className="border-b border-term-border py-1 text-left font-normal">To</th>
          <th className="border-b border-term-border py-1 text-right font-normal">Held</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={`${row.date}-${row.state}`}
            className={isFailed(row.state) ? 'bg-sig-down/[0.07]' : ''}
            data-testid={`transition-${row.date}`}
          >
            <td className="py-1 font-mono tabular-nums text-term-muted">
              {shortDate(row.date)}
            </td>
            <td className="py-1 text-term-dim">{stateLabel(row.from_state)}</td>
            <td className="py-1">
              <span
                className={`inline-block rounded-sm border px-1.5 py-0.5 uppercase tracking-wide ${stateClasses(
                  row.state,
                )}`}
              >
                {stateLabel(row.state)}
              </span>
            </td>
            <td className="py-1 text-right font-mono tabular-nums text-term-muted">
              {bars(row.bars_in_state)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DarkTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-sm border border-term-border bg-term-bg px-2 py-1.5 text-2xs shadow-lg">
      <div className="mb-1 font-mono text-term-muted">{label}</div>
      {payload.map((entry: any) => (
        <div key={entry.name} className="flex justify-between gap-4 font-mono tabular-nums">
          <span style={{ color: entry.stroke }}>{entry.name}</span>
          <span className="text-term-text">
            {entry.name?.endsWith('d') && !entry.name.includes('high') && !entry.name.includes('low')
              ? signed(entry.value)
              : num(entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
}
