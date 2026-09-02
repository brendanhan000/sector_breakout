import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api } from '../src/lib/api';
import { regimeResponse, sectorsResponse } from './fixtures';

function mockFetch(body: unknown, status = 200) {
  const fn = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api client', () => {
  it('requests the expected paths', async () => {
    const fetchMock = mockFetch(sectorsResponse());
    await api.sectors();
    expect(fetchMock).toHaveBeenCalledWith('/api/sectors', expect.anything());
  });

  it('passes the plane through to the leaderboard', async () => {
    const fetchMock = mockFetch({ entries: [] });
    await api.leaderboard('absolute');
    expect(fetchMock).toHaveBeenCalledWith('/api/leaderboard?plane=absolute', expect.anything());
  });

  it('encodes the symbol in history requests', async () => {
    const fetchMock = mockFetch({ series: [] });
    await api.history('XLK', 'relative', 60);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sectors/XLK/history?plane=relative&days=60',
      expect.anything(),
    );
  });

  it('surfaces a 503 as a typed ApiError so the UI can show an empty state', async () => {
    mockFetch({ detail: 'No completed run yet. POST /api/refresh.' }, 503);
    await expect(api.regime()).rejects.toBeInstanceOf(ApiError);
    await expect(api.regime()).rejects.toMatchObject({ status: 503 });
  });

  it('parses a regime payload', async () => {
    mockFetch(regimeResponse({ low_dispersion: true }));
    const result = await api.regime();
    expect(result.low_dispersion).toBe(true);
    expect(result.benchmark.symbol).toBe('SPY');
    expect(result.correlation_sparkline).toHaveLength(5);
  });
});
