import { useCallback, useState } from 'react';

import { DetailDrawer } from './components/DetailDrawer';
import { RRGChart } from './components/RRGChart';
import { RegimeHeader } from './components/RegimeHeader';
import { StateGrid } from './components/StateGrid';
import { useDashboard, useSectorHistory } from './hooks/useDashboard';
import { ApiError } from './lib/api';
import type { Plane } from './types/api';

export default function App() {
  const { regime, sectors, rrg, triggerRefresh, refreshing } = useDashboard();
  const [selected, setSelected] = useState<string | null>(null);
  const [drawerPlane, setDrawerPlane] = useState<Plane>('absolute');

  const history = useSectorHistory(selected, drawerPlane, 250);

  const handleSelect = useCallback((symbol: string) => {
    setSelected((current) => (current === symbol ? null : symbol));
  }, []);

  // 503 is the expected pre-first-run state, not an error.
  const notReady =
    regime.error instanceof ApiError && regime.error.status === 503;

  // In a low-dispersion regime every Plane B signal is noise. Graying the
  // relative plane out is a correctness feature, not decoration.
  const lowDispersion = regime.data?.low_dispersion ?? false;

  return (
    <div className="flex h-full min-w-[1440px] flex-col bg-term-bg">
      <RegimeHeader
        regime={regime.data}
        loading={regime.loading}
        refreshing={refreshing}
        onRefresh={triggerRefresh}
      />

      {notReady ? (
        <EmptyState onRefresh={triggerRefresh} refreshing={refreshing} />
      ) : (
        <main className="flex min-h-0 flex-1 flex-col">
          {/* The RRG is the hero: roughly 55% of the viewport. */}
          <div className="min-h-0 flex-[55] border-b border-term-border">
            <RRGChart
              data={rrg.data}
              loading={rrg.loading}
              dimmed={lowDispersion}
              onSelect={handleSelect}
              highlighted={selected}
            />
          </div>

          <div className="min-h-0 flex-[45]">
            <StateGrid
              data={sectors.data}
              loading={sectors.loading}
              onSelect={handleSelect}
              selected={selected}
            />
          </div>
        </main>
      )}

      <DetailDrawer
        symbol={selected}
        history={history.data}
        loading={history.loading}
        plane={drawerPlane}
        onPlaneChange={setDrawerPlane}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}

function EmptyState({
  onRefresh,
  refreshing,
}: {
  onRefresh: () => void;
  refreshing: boolean;
}) {
  return (
    <div className="flex flex-1 items-center justify-center">
      <div className="max-w-md text-center">
        <p className="mb-2 text-sm text-term-text">No completed run yet.</p>
        <p className="mb-4 text-2xs leading-relaxed text-term-muted">
          The dashboard will not render a partially computed day. Trigger a refresh to
          backfill five years of daily bars and compute the first set of signals.
        </p>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="rounded-sm border border-term-border px-4 py-1.5 text-2xs uppercase tracking-wide text-term-muted hover:border-term-muted hover:text-term-text disabled:opacity-40"
        >
          {refreshing ? 'Running…' : 'Run first refresh'}
        </button>
      </div>
    </div>
  );
}
