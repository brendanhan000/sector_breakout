/**
 * Response types mirroring backend/api/schemas.py.
 *
 * Kept hand-written rather than generated so the shape the UI depends on is
 * reviewable in a diff. If these drift from the Pydantic models the type
 * checker will not catch it — tests/api.test.ts asserts the parsing instead.
 */

export type Plane = 'absolute' | 'relative';

export type DataQuality = 'OK' | 'WARMING_UP' | 'DEGRADED' | 'STALE';

export type SectorState =
  | 'NEUTRAL'
  | 'PENDING_UP'
  | 'CONFIRMED_UP'
  | 'FAILED_UP'
  | 'PENDING_DOWN'
  | 'CONFIRMED_DOWN'
  | 'FAILED_DOWN';

export type Quadrant = 'LEADING' | 'WEAKENING' | 'LAGGING' | 'IMPROVING';

export interface Envelope {
  as_of: string | null;
  data_quality: DataQuality;
}

export interface HealthResponse extends Envelope {
  status: 'ok' | 'degraded' | 'empty';
  database: boolean;
  latest_run_id: string | null;
  last_run_at: string | null;
}

export interface SparkPoint {
  date: string;
  value: number | null;
}

export interface BenchmarkView {
  symbol: string;
  state: SectorState | null;
  bars_in_state: number | null;
}

export interface RegimeResponse extends Envelope {
  dispersion: number | null;
  dispersion_raw: number | null;
  dispersion_percentile: number | null;
  low_dispersion: boolean;
  low_dispersion_threshold: number;
  correlation: number | null;
  correlation_sparkline: SparkPoint[];
  benchmark: BenchmarkView;
  message: string | null;
}

export interface HorizonPoint {
  horizon: number;
  signal: number | null;
  c: number | null;
  z_up: number | null;
  z_dn: number | null;
  channel_max: number | null;
  channel_min: number | null;
}

export interface PlaneView {
  plane: Plane;
  state: SectorState | null;
  bars_in_state: number | null;
  term: HorizonPoint[];
  rvol: number | null;
  narrow: boolean;
  breadth_spread: number | null;
  equal_weight_signal: number | null;
  z_up: number | null;
  z_dn: number | null;
  close: number | null;
}

export interface SectorRow {
  symbol: string;
  name: string;
  equal_weight: string | null;
  quarantined: boolean;
  absolute: PlaneView | null;
  relative: PlaneView | null;
  beta: number | null;
  disagreement: boolean;
  quadrant: Quadrant | null;
}

export interface SectorsResponse extends Envelope {
  state_horizon: number;
  horizons: number[];
  low_dispersion: boolean;
  sectors: SectorRow[];
}

export interface HistoryPoint {
  date: string;
  close: number | null;
  high: number | null;
  low: number | null;
  residual: number | null;
  beta: number | null;
  rvol: number | null;
  state: SectorState | null;
  bars_in_state: number | null;
  entered: boolean;
  from_state: SectorState | null;
  term: Record<string, Record<string, number | null>>;
}

export interface HistoryResponse extends Envelope {
  symbol: string;
  name: string;
  plane: Plane;
  days: number;
  series: HistoryPoint[];
  transitions: HistoryPoint[];
}

export interface RRGTailPoint {
  date: string;
  rs_ratio: number;
  rs_momentum: number;
  quadrant: Quadrant | null;
}

export interface RRGSeries {
  symbol: string;
  name: string;
  rs_ratio: number | null;
  rs_momentum: number | null;
  quadrant: Quadrant | null;
  tail: RRGTailPoint[];
}

export interface RRGResponse extends Envelope {
  origin: number;
  tail_length: number;
  series: RRGSeries[];
}

export interface LeaderboardEntry {
  rank: number;
  symbol: string;
  name: string;
  plane: Plane;
  z_up: number | null;
  z_dn: number | null;
  signal: number | null;
  state: SectorState | null;
  bars_in_state: number | null;
  narrow: boolean;
  disagreement: boolean;
}

export interface LeaderboardResponse extends Envelope {
  plane: Plane;
  horizon: number;
  low_dispersion: boolean;
  entries: LeaderboardEntry[];
}

export interface RunResponse {
  run_id: string;
  status: string;
  trigger: string;
  provider: string;
  started_at: string;
  finished_at: string | null;
  as_of: string | null;
  data_quality: DataQuality;
  symbols_requested: number;
  symbols_ingested: number;
  bars_upserted: number;
  quarantined: Array<Record<string, unknown>>;
  warnings: Array<Record<string, unknown>>;
  repulled: string[];
  error: string | null;
}

export interface RefreshResponse {
  run_id: string;
  status: string;
  accepted_at: string;
}
