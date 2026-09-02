import { planesDisagree } from '../src/lib/signals';
import type {
  HistoryResponse,
  PlaneView,
  RRGResponse,
  RegimeResponse,
  SectorRow,
  SectorState,
  SectorsResponse,
} from '../src/types/api';

export function planeView(overrides: Partial<PlaneView> = {}): PlaneView {
  return {
    plane: 'absolute',
    state: 'NEUTRAL',
    bars_in_state: 4,
    term: [
      { horizon: 10, signal: 0.2, c: 0.2, z_up: -1, z_dn: -1, channel_max: 110, channel_min: 90 },
      { horizon: 20, signal: 0.1, c: 0.1, z_up: -1.5, z_dn: -2, channel_max: 112, channel_min: 88 },
      { horizon: 55, signal: 0.05, c: 0.05, z_up: -2, z_dn: -3, channel_max: 120, channel_min: 80 },
    ],
    rvol: 1.05,
    narrow: false,
    breadth_spread: 0.05,
    equal_weight_signal: 0.05,
    z_up: -1.5,
    z_dn: -2,
    close: 100,
    ...overrides,
  };
}

export function sectorRow(
  symbol: string,
  absolute: SectorState,
  relative: SectorState,
  overrides: Partial<SectorRow> = {},
): SectorRow {
  return {
    symbol,
    name: `${symbol} sector`,
    equal_weight: `RSP${symbol.slice(-1)}`,
    quarantined: false,
    absolute: planeView({ plane: 'absolute', state: absolute }),
    relative: planeView({ plane: 'relative', state: relative }),
    beta: 1.05,
    disagreement: planesDisagree(absolute, relative),
    quadrant: 'LEADING',
    ...overrides,
  };
}

export function sectorsResponse(overrides: Partial<SectorsResponse> = {}): SectorsResponse {
  return {
    as_of: '2024-06-28',
    data_quality: 'OK',
    state_horizon: 20,
    horizons: [10, 20, 55],
    low_dispersion: false,
    sectors: [
      sectorRow('XLK', 'CONFIRMED_UP', 'CONFIRMED_UP'),
      sectorRow('XLU', 'CONFIRMED_UP', 'CONFIRMED_DOWN'), // disagreement
      sectorRow('XLE', 'CONFIRMED_DOWN', 'PENDING_UP'),   // disagreement
      sectorRow('XLV', 'NEUTRAL', 'NEUTRAL'),
    ],
    ...overrides,
  };
}

export function regimeResponse(overrides: Partial<RegimeResponse> = {}): RegimeResponse {
  return {
    as_of: '2024-06-28',
    data_quality: 'OK',
    dispersion: 0.0085,
    dispersion_raw: 0.0091,
    dispersion_percentile: 62.5,
    low_dispersion: false,
    low_dispersion_threshold: 25,
    correlation: 0.68,
    correlation_sparkline: [
      { date: '2024-06-24', value: 0.6 },
      { date: '2024-06-25', value: 0.63 },
      { date: '2024-06-26', value: 0.66 },
      { date: '2024-06-27', value: 0.67 },
      { date: '2024-06-28', value: 0.68 },
    ],
    benchmark: { symbol: 'SPY', state: 'CONFIRMED_UP', bars_in_state: 42 },
    message: null,
    ...overrides,
  };
}

export function rrgResponse(overrides: Partial<RRGResponse> = {}): RRGResponse {
  const tail = (base: number, momentum: number) =>
    Array.from({ length: 10 }, (_, i) => ({
      date: `2024-06-${String(14 + i).padStart(2, '0')}`,
      rs_ratio: base + i * 0.1,
      rs_momentum: momentum + i * 0.05,
      quadrant: (base + i * 0.1 > 100
        ? momentum + i * 0.05 > 100
          ? 'LEADING'
          : 'WEAKENING'
        : momentum + i * 0.05 > 100
          ? 'IMPROVING'
          : 'LAGGING') as RRGResponse['series'][0]['quadrant'],
    }));

  return {
    as_of: '2024-06-28',
    data_quality: 'OK',
    origin: 100,
    tail_length: 10,
    series: [
      { symbol: 'XLK', name: 'Technology', rs_ratio: 101.9, rs_momentum: 100.9, quadrant: 'LEADING', tail: tail(101, 100.5) },
      { symbol: 'XLU', name: 'Utilities', rs_ratio: 98.9, rs_momentum: 98.9, quadrant: 'LAGGING', tail: tail(98, 98.5) },
    ],
    ...overrides,
  };
}

export function historyResponse(overrides: Partial<HistoryResponse> = {}): HistoryResponse {
  const series = Array.from({ length: 30 }, (_, i) => ({
    date: `2024-05-${String(i + 1).padStart(2, '0')}`,
    close: 100 + i,
    high: 101 + i,
    low: 99 + i,
    residual: 100 + i * 0.3,
    beta: 1.1,
    rvol: 1.0 + (i % 5) * 0.1,
    state: 'CONFIRMED_UP' as SectorState,
    bars_in_state: i,
    entered: i === 0,
    from_state: i === 0 ? ('PENDING_UP' as SectorState) : null,
    term: {
      '10': { signal: 0.8, c: 0.9, z_up: 0.5, z_dn: -3, max: 100 + i, min: 90 + i },
      '20': { signal: 0.7, c: 0.8, z_up: 0.3, z_dn: -3, max: 101 + i, min: 89 + i },
      '55': { signal: 0.6, c: 0.7, z_up: 0.1, z_dn: -3, max: 103 + i, min: 85 + i },
    },
  }));

  return {
    as_of: '2024-06-28',
    data_quality: 'OK',
    symbol: 'XLK',
    name: 'Technology',
    plane: 'absolute',
    days: 30,
    series,
    transitions: [
      {
        ...series[0],
        date: '2024-04-02',
        state: 'FAILED_UP' as SectorState,
        from_state: 'CONFIRMED_UP' as SectorState,
        entered: true,
        bars_in_state: 0,
      },
      { ...series[0], entered: true },
    ],
    ...overrides,
  };
}
