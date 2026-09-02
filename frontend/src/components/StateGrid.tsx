import type { PlaneView, SectorRow, SectorsResponse } from '../types/api';
import { num, signed } from '../lib/format';
import { StateBadge } from './StateBadge';
import { TermStructure } from './TermStructure';

interface Props {
  data: SectorsResponse | null;
  loading: boolean;
  onSelect: (symbol: string) => void;
  selected: string | null;
}

/**
 * 11 rows x 2 plane columns.
 *
 * Rows where the two planes disagree are given a left accent and a tinted
 * background. Those rows are the entire reason the dashboard has two planes:
 * on an up day the absolute column shows eleven green lights, which is one bit
 * of information displayed eleven times. The disagreements are the signal.
 */
export function StateGrid({ data, loading, onSelect, selected }: Props) {
  if (!data) {
    return (
      <div className="px-4 py-8 text-sm text-term-dim">
        {loading ? 'Loading sectors…' : 'No sector data.'}
      </div>
    );
  }

  const disagreements = data.sectors.filter((s) => s.disagreement).length;

  return (
    <section className="flex min-h-0 flex-col">
      <div className="flex items-baseline gap-3 border-b border-term-border px-4 py-2">
        <h2 className="text-2xs uppercase tracking-wider text-term-muted">State grid</h2>
        <span className="text-2xs text-term-dim">
          {data.sectors.length} sectors · state machine at N={data.state_horizon} · term
          structure {data.horizons.join('/')}d
        </span>
        {disagreements > 0 && (
          <span className="ml-auto text-2xs text-sig-warn" data-testid="disagreement-count">
            {disagreements} plane disagreement{disagreements === 1 ? '' : 's'}
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-term-panel">
            <tr className="text-2xs uppercase tracking-wider text-term-dim">
              <th className="border-b border-term-border px-3 py-1.5 text-left font-normal">
                Sector
              </th>
              <th className="border-b border-term-border px-2 py-1.5 text-right font-normal">
                β
              </th>
              <PlaneHeader label="Absolute — is beta on?" />
              <PlaneHeader label="Relative — who is rotating?" dimmed={data.low_dispersion} />
              <th className="border-b border-term-border px-2 py-1.5 text-left font-normal">
                Quadrant
              </th>
            </tr>
          </thead>
          <tbody>
            {data.sectors.map((row) => (
              <Row
                key={row.symbol}
                row={row}
                lowDispersion={data.low_dispersion}
                selected={selected === row.symbol}
                onSelect={onSelect}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function PlaneHeader({ label, dimmed = false }: { label: string; dimmed?: boolean }) {
  return (
    <th
      colSpan={5}
      className={`border-b border-l border-term-border px-2 py-1.5 text-left font-normal ${
        dimmed ? 'text-term-dim' : ''
      }`}
    >
      {label}
      {dimmed && <span className="ml-1 text-sig-warn/70">(low dispersion)</span>}
    </th>
  );
}

function Row({
  row,
  lowDispersion,
  selected,
  onSelect,
}: {
  row: SectorRow;
  lowDispersion: boolean;
  selected: boolean;
  onSelect: (symbol: string) => void;
}) {
  const emphasis = row.disagreement
    ? 'border-l-2 border-l-sig-warn bg-sig-warn/[0.06]'
    : 'border-l-2 border-l-transparent';

  return (
    <tr
      onClick={() => onSelect(row.symbol)}
      className={`cursor-pointer border-b border-term-grid transition-colors hover:bg-term-raised ${emphasis} ${
        selected ? 'bg-term-raised' : ''
      }`}
      data-testid={`sector-row-${row.symbol}`}
      data-disagreement={row.disagreement}
    >
      <td className="px-3 py-1.5">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-term-text">{row.symbol}</span>
          <span className="truncate text-2xs text-term-muted">{row.name}</span>
          {row.quarantined && (
            <span
              className="rounded-sm border border-sig-down/50 px-1 text-2xs uppercase text-sig-down"
              title="Data failed ingest validation; no signals computed"
            >
              quarantined
            </span>
          )}
        </div>
      </td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-term-muted">
        {num(row.beta)}
      </td>

      <PlaneCells view={row.absolute} />
      <PlaneCells view={row.relative} dimmed={lowDispersion} leading />

      <td className="px-2 py-1.5">
        <span className="text-2xs uppercase tracking-wide text-term-muted">
          {row.quadrant ?? '—'}
        </span>
      </td>
    </tr>
  );
}

function PlaneCells({
  view,
  dimmed = false,
  leading = false,
}: {
  view: PlaneView | null;
  dimmed?: boolean;
  leading?: boolean;
}) {
  const dim = dimmed ? 'opacity-40' : '';
  const border = leading ? 'border-l border-term-border' : '';

  if (!view) {
    return (
      <>
        <td className={`px-2 py-1.5 text-term-dim ${border}`}>—</td>
        <td className="px-2 py-1.5" />
        <td className="px-2 py-1.5" />
        <td className="px-2 py-1.5" />
        <td className="px-2 py-1.5" />
      </>
    );
  }

  return (
    <>
      <td className={`px-2 py-1.5 ${border} ${dim}`}>
        <StateBadge state={view.state} barsInState={view.bars_in_state} />
      </td>
      <td className={`px-2 py-1.5 ${dim}`}>
        <TermStructure term={view.term} />
      </td>
      <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${dim}`}>
        <span title="Extension beyond the channel, in ATR units">
          {signed(view.z_up ?? null)}
        </span>
      </td>
      <td className={`px-2 py-1.5 text-right font-mono tabular-nums ${dim}`}>
        <span
          className={view.rvol !== null && view.rvol > 1.3 ? 'text-term-text' : 'text-term-muted'}
          title="Relative volume vs its own 20-day EWM baseline"
        >
          {num(view.rvol)}
        </span>
      </td>
      <td className={`px-2 py-1.5 ${dim}`}>
        {view.narrow && (
          <span
            className="rounded-sm border border-sig-warn/60 bg-sig-warn/15 px-1.5 py-0.5 text-2xs font-medium uppercase tracking-wide text-sig-warn"
            data-testid="narrow-badge"
            title="Cap-weight ETF is breaking out but its equal-weight twin is not. Four mega-caps moving, not a sector."
          >
            narrow
          </span>
        )}
      </td>
    </>
  );
}
