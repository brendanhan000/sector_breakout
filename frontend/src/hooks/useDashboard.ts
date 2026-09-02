import { useCallback, useState } from 'react';

import { api } from '../lib/api';
import type { HistoryResponse, Plane } from '../types/api';
import { useApiResource } from './useApiResource';

/** All top-level panels, refreshed together so they can never disagree on
 *  `as_of` — a header showing one date above a grid showing another is worse
 *  than showing nothing. */
export function useDashboard() {
  const [nonce, setNonce] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const regime = useApiResource((s) => api.regime(s), [nonce]);
  const sectors = useApiResource((s) => api.sectors(s), [nonce]);
  const rrg = useApiResource((s) => api.rrg(s), [nonce]);

  const reloadAll = useCallback(() => setNonce((n) => n + 1), []);

  /** Kick off a server-side refresh, then poll the run until it settles. */
  const triggerRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const { run_id } = await api.refresh();
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const run = await api.run(run_id);
        if (run.status !== 'RUNNING') break;
      }
      reloadAll();
    } finally {
      setRefreshing(false);
    }
  }, [reloadAll]);

  return { regime, sectors, rrg, reloadAll, triggerRefresh, refreshing };
}

export function useSectorHistory(symbol: string | null, plane: Plane, days = 250) {
  return useApiResource<HistoryResponse | null>(
    async (signal) => (symbol ? api.history(symbol, plane, days, signal) : null),
    [symbol, plane, days],
  );
}
