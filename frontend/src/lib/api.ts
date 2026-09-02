/** Thin fetch wrapper. No client-side caching layer — one user, one screen. */

import type {
  HealthResponse,
  HistoryResponse,
  LeaderboardResponse,
  Plane,
  RRGResponse,
  RefreshResponse,
  RegimeResponse,
  RunResponse,
  SectorsResponse,
} from '../types/api';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const BASE = '/api';

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { signal });

  if (!response.ok) {
    let detail: string | undefined;
    try {
      detail = (await response.json())?.detail;
    } catch {
      detail = undefined;
    }
    // 503 is the expected "no completed run yet" state, not a crash. The UI
    // renders it as an empty-state panel rather than an error toast.
    throw new ApiError(
      detail ?? `Request to ${path} failed`,
      response.status,
      detail,
    );
  }

  return (await response.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) => get<HealthResponse>('/health', signal),
  regime: (signal?: AbortSignal) => get<RegimeResponse>('/regime', signal),
  sectors: (signal?: AbortSignal) => get<SectorsResponse>('/sectors', signal),
  rrg: (signal?: AbortSignal) => get<RRGResponse>('/rrg', signal),

  leaderboard: (plane: Plane = 'relative', signal?: AbortSignal) =>
    get<LeaderboardResponse>(`/leaderboard?plane=${plane}`, signal),

  history: (symbol: string, plane: Plane, days: number, signal?: AbortSignal) =>
    get<HistoryResponse>(
      `/sectors/${encodeURIComponent(symbol)}/history?plane=${plane}&days=${days}`,
      signal,
    ),

  run: (runId: string, signal?: AbortSignal) =>
    get<RunResponse>(`/runs/${encodeURIComponent(runId)}`, signal),

  refresh: async (): Promise<RefreshResponse> => {
    const response = await fetch(`${BASE}/refresh`, { method: 'POST' });
    if (!response.ok) {
      throw new ApiError('Refresh could not be started', response.status);
    }
    return (await response.json()) as RefreshResponse;
  },
};
