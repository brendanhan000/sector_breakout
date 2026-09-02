# Sector ETF Breakout & Rotation Dashboard

Detects upward and downward breakouts across the 11 SPDR sector ETFs on **two
independent planes**:

| Plane | Measures | Answers |
|---|---|---|
| **A — Absolute** | breakout in raw price terms | *Is beta on?* |
| **B — Relative** | breakout in beta-adjusted terms vs SPY | *Which sectors are actually rotating?* |

Sector ETFs run 0.70–0.90 correlated with each other. A naive absolute-only
dashboard shows eleven green lights on an up day, which is one bit of
information displayed eleven times. **The value of this system lives in the
disagreements between the two planes** — XLU breaking out absolute while
breaking down relative is defensive drift inside a rally; XLE breaking out
relative while breaking down absolute is genuine rotation into energy during a
selloff. Rows where the planes disagree are visually emphasised in the grid.

---

## Quick start

Requires **Python 3.11+** (the engine uses `StrEnum` and PEP 604 unions).

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Then run everything through `.venv/bin/python` (or `source .venv/bin/activate`
first). The `python -m pip` form above is deliberate — see *Troubleshooting*.

Point it at a database and apply migrations. **SQLite needs no server** and is
the fastest way to see the dashboard working:

```bash
echo 'SECTOR_DATABASE_URL=sqlite:///./sector_breakout.db' > .env
.venv/bin/alembic -c backend/db/alembic.ini upgrade head
```

PostgreSQL is the production target — see *Storage* below for both.

Run the first refresh (backfills 5 years, computes every signal, persists):

```bash
.venv/bin/python -m backend.jobs.cli refresh
```

Serve the API:

```bash
.venv/bin/uvicorn backend.api.app:app --factory --port 8000
```

Serve the dashboard:

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` to port 8000.

---

## Storage

The schema is plain SQLAlchemy 2.x with Alembic migrations, and the one
dialect-specific type (`PortableJSON`) resolves to **JSONB on PostgreSQL** and
plain JSON elsewhere. Both back ends are real options:

| | SQLite | PostgreSQL |
|---|---|---|
| Setup | none — a file | server install |
| JSON column | `JSON` | `JSONB` (indexable, binary) |
| Concurrency | single writer | fine |
| Suitable for | development, a single-user desktop dashboard | production |

For a read-only daily-bar dashboard at 23 symbols × 1 write/day, SQLite is
genuinely adequate. PostgreSQL is the documented target and what the type
choices are tuned for.

```bash
# SQLite — no server
SECTOR_DATABASE_URL=sqlite:///./sector_breakout.db

# PostgreSQL
SECTOR_DATABASE_URL=postgresql+psycopg2://localhost:5432/sector_breakout
```

### Installing PostgreSQL on macOS

`createdb: command not found` just means no Postgres is installed. Either:

* **[Postgres.app](https://postgresapp.com)** — download, drag to
  `/Applications`, click *Initialize*. Then add its binaries to `PATH`:
  ```bash
  echo 'export PATH="/Applications/Postgres.app/Contents/Versions/latest/bin:$PATH"' >> ~/.zshrc
  ```
* **Homebrew** (if you have it, or after installing it from
  [brew.sh](https://brew.sh)):
  ```bash
  brew install postgresql@16 && brew services start postgresql@16
  ```

Then:

```bash
createdb sector_breakout
# switch the line in .env, then:
.venv/bin/alembic -c backend/db/alembic.ini upgrade head
.venv/bin/python -m backend.jobs.cli refresh
```

> **Caveat.** Everything in this repo — the full test suite, migrations up and
> down, and live 23-symbol backfills — has been exercised against SQLite. The
> PostgreSQL branch of the `ON CONFLICT` upsert in `data/ingest.py` and
> `db/persist.py` is written but has not been run against a live server. Verify
> it before trusting a production deployment.

---

## Configuration

**`config.yaml` holds every threshold, window and universe member.** There are no
magic numbers in `backend/engine/` — a test (`test_config.py`) parses the state
machine's AST and fails if a numeric literal appears in the transition logic.

Secrets never go in `config.yaml`. They come from the environment, prefixed
`SECTOR_`:

| Variable | Purpose | Required |
|---|---|---|
| `SECTOR_DATABASE_URL` | SQLAlchemy URL | yes |
| `SECTOR_CONFIG_PATH` | override config.yaml location | no |
| `SECTOR_LOG_LEVEL` | default `INFO` | no |
| `SECTOR_SCHWAB_CLIENT_ID` | Schwab app key | Schwab only |
| `SECTOR_SCHWAB_CLIENT_SECRET` | Schwab app secret | Schwab only |
| `SECTOR_SCHWAB_REFRESH_TOKEN` | OAuth2 refresh token | Schwab only |
| `SECTOR_SCHWAB_BASE_URL` | default `https://api.schwabapi.com` | no |
| `SECTOR_SCHWAB_TOKEN_URL` | default `.../v1/oauth/token` | no |

### Switching to Schwab

1. Register an app at [developer.schwab.com](https://developer.schwab.com) with
   the **Accounts and Trading Production** product and a callback URL of
   `https://127.0.0.1`.
2. Complete the three-legged OAuth flow once to obtain a **refresh token**.
   Schwab refresh tokens expire every **7 days** — renewing them is a manual
   step, by design on Schwab's side.
3. Export the three `SECTOR_SCHWAB_*` variables above.
4. Set `data.provider: schwab` in `config.yaml`.

The access-token handshake sits behind the `TokenProvider` interface
(`backend/data/schwab_provider.py`), so it can be substituted in tests without a
live account. Tokens are cached in memory only — a token written to disk is a
credential written to disk. Token-refresh failures deliberately do not echo the
response body, which can contain the credential that was just rejected.

---

## The formulas

Everything below is implemented in `backend/engine/`, which is pure: no I/O, no
database, no clock, no global state. A test asserts the package imports nothing
from the application layer and never reads the clock, because that purity is
what makes the no-look-ahead test a real experiment rather than a formality.

### 1. Wilder ATR — `engine/atr.py`

```
TR_t  = max(High_t − Low_t, |High_t − Close_{t−1}|, |Low_t − Close_{t−1}|)
ATR_t = (ATR_{t−1} × (N − 1) + TR_t) / N            N = 20 default
```

Seeded with the simple mean of the first N true ranges (`adjust=False`
semantics). The first bar has no previous close, so its TR degenerates to the
high−low span.

The seed is taken from the first window of N **consecutive valid** true ranges,
not necessarily from bar 0. This matters: the residual plane is NaN through the
entire beta warm-up, and an implementation that only seeds from bar 0 returns an
all-NaN ATR for the whole relative plane — which silently makes `z_up`/`z_dn`
undefined and leaves the leaderboard sorting on nothing.

### 2. Rolling beta and the residual series — `engine/beta.py`

```
r_i     = log(Close_i / Close_i.shift(1))
r_m     = log(Close_SPY / Close_SPY.shift(1))
β_i,t   = Cov(r_i, r_m)_60d / Var(r_m)_60d
ε_i,t   = r_i,t − β_i,t · r_m,t
R_i,t   = 100 × exp(cumsum(ε_i))
```

Sector betas range from about 0.5 (XLU, XLP) to 1.2+ (XLK, XLY). A raw
`XLK / SPY` ratio bakes beta drift into what it calls "relative strength" — on
any strong up day the high-beta sectors look like they are rotating in when all
they are doing is having more beta. Subtracting `β · r_m` removes the market
component. This is why the residual construction is required rather than
optional, and there is a test asserting the naive ratio *fails* the
orthogonality check that the residual passes.

`R` is price-*like*, so the identical channel code runs on both planes.
Synthetic OHLC is the sector's raw OHLC scaled by `R_t / Close_t`, which
preserves intrabar geometry and makes the synthetic close exactly `R`. **Volume
is carried through unscaled** — it is a real, plane-independent quantity and the
RVOL gate must see the actual traded volume on both planes.

### 3. Continuous channel position — `engine/channel.py`

Run on both `Close` (Plane A) and `R` (Plane B), at **N ∈ {10, 20, 55}**.

```
max_N   = High.rolling(N).max().shift(1)      ← the .shift(1) is mandatory
min_N   = Low.rolling(N).min().shift(1)
mid     = (max_N + min_N) / 2
width   = max_N − min_N
c_t     = clip((Close_t − mid) / (0.5 × width), −1.5, +1.5)
signal  = c.ewm(span=max(2, N // 4), adjust=False).mean()
```

**The `.shift(1)` is the single most important line in the codebase.**
`High.rolling(N).max()` at bar *t* includes bar *t*'s own high. Comparing bar
*t*'s close against a channel that already contains bar *t* is circular, and any
breakout it "detects" was constructed from information that did not exist when
the bar opened. `tests/test_no_lookahead.py` exists for this.

`width == 0` returns `0.0` — not NaN (which would poison the EWM for every later
bar) and not inf (which would blow up every downstream comparison).

**The three horizons are stored as a term structure and never averaged.** The
shape is the diagnostic:

| Shape | Reading |
|---|---|
| All three positive, rising | Established trend |
| Short positive, long negative | Early reversal or bounce |
| Short negative, long positive | Pullback inside uptrend (the good entry) |
| All three pinned near ±1.5 | Extended, expect mean reversion |

A composite average collapses all four into one number. There is a frontend test
demonstrating two opposite readings that average to the identical value.

### 4. Extension — the cross-sectional sort key

```
z_up = (Close_t − max_N) / ATR_N
z_dn = (min_N − Close_t) / ATR_N
```

Channel position saturates at ±1.5 and cannot distinguish "just broke out" from
"20% beyond the channel". Extension can, and being ATR-normalised it is directly
comparable across XLU and XLK — which is what makes it a valid sort key for the
rotation leaderboard.

### 5. Confirmation filters — `engine/filters.py`

```
RVOL_t = Volume_t / Volume.ewm(span=20, adjust=False).mean().shift(1)
```

Confirmed breakouts require `RVOL > 1.3`. The `.shift(1)` matters for the same
reason as in the channel: without it a huge volume day partially normalises away
its own surge.

```
breadth_spread = signal_capweight(N=20) − signal_equalweight(N=20)
```

If the cap-weight ETF confirms a breakout while its equal-weight twin has
`signal < 0.30`, the row is flagged **NARROW** — four mega-caps moving, not a
sector. It is a distinct badge in the grid, not a tooltip. Breadth is measured on
the absolute plane for both planes' badges: "is the whole sector moving?" is a
question about constituents, not about the residual construction.

**Persistence:** confirmation uses closes, never intraday touches, and requires
**two consecutive closes** beyond the level. A close exactly *at* the prior high
has not broken it (strict inequality).

### 6. State machine — `engine/state.py`

Per sector, per plane, at N=20.

```
NEUTRAL        → PENDING_UP      when signal > +0.90
PENDING_UP     → CONFIRMED_UP    when 2nd consecutive close beyond max_N AND RVOL > 1.3
PENDING_UP     → NEUTRAL         when signal < +0.50
CONFIRMED_UP   → NEUTRAL         when signal < +0.30
CONFIRMED_UP   → FAILED_UP       when signal < 0 within 5 bars of confirmation
FAILED_UP      → NEUTRAL         after 10 bars
(mirrored for the downside)
```

**Asymmetric entry/exit (0.90 in, 0.30 out) is mandatory.** The gap is
hysteresis; symmetric thresholds make a signal loitering at the boundary flip
state almost every bar and the grid becomes unreadable. `config.yaml` validation
*rejects* a configuration where `pending_entry > pending_exit > confirmed_exit`
does not hold.

At most one transition is evaluated per bar, which is what guarantees no state
is skipped and no oscillation happens within a bar. A crash cannot jump
`PENDING_UP → PENDING_DOWN`; it routes through `NEUTRAL` on the next bar.

`bars_in_state` is exposed everywhere — 0 on the bar a state is entered. A
40-day-old breakout and a 2-day-old one are entirely different trades.

`FAILED_*` is an explicit terminal state with persisted history. Failed
breakouts are the highest-quality reversal signal in the system, and the detail
drawer keeps them visible long after the machine cools back to NEUTRAL. The
failure check is evaluated *before* the fade check, so "reversed outright" stays
distinguishable from "faded".

### 7. Regime — `engine/regime.py`

```
spy_state  = state machine on SPY, Plane A
dispersion = cross-sectional stdev of the 11 sector daily returns
           → 20-day rolling mean
           → percentile rank over trailing 756 bars (3y)
correlation = mean of off-diagonal elements of the 60-day rolling
              correlation matrix of the 11 sectors
```

**When `dispersion_percentile < 25`, the entire relative plane grays itself out**
with the message *"Low dispersion — rotation signals unreliable."* This is not
decoration. In a low-dispersion regime every Plane B signal is noise, and a
dashboard that keeps rendering confident rotation calls is actively harmful.

The percentile requires its full trailing window — a "3-year percentile"
computed from 200 observations is a different statistic wearing the same label,
and the quartile gate would fire at the wrong times. Where the percentile is
unknown, `low_dispersion` is `false` and the API reports `WARMING_UP` separately;
missing history is not evidence of low dispersion.

### 8. RRG — `engine/rrg.py`

```
RS          = 100 × (Close_sector / Close_SPY)
RS_Ratio    = 101 + (RS − SMA(RS, 60)) / StDev(RS, 60)
RS_Momentum = 101 + (RS_Ratio − SMA(RS_Ratio, 60)) / StDev(RS_Ratio, 60)
```

| Quadrant | Condition | Colour |
|---|---|---|
| Leading | x > 100, y > 100 | green |
| Weakening | x > 100, y < 100 | yellow |
| Lagging | x < 100, y < 100 | red |
| Improving | x < 100, y > 100 | blue |

Rotation reads **counterclockwise**: Improving → Leading → Weakening → Lagging.
A static scatter tells you position; the tail tells you direction, and direction
is the trade. Tails are not optional — 10 trailing daily points per sector,
oldest-first so direction renders correctly.

> **Provenance.** This is a documented reconstruction of the standard RRG
> normalisation from its published description, not the proprietary commercial
> implementation (RRG® is a registered trademark of RRG Research). The quadrant
> geometry and the rotational reading are the same; the exact commercial
> smoothing constants are not public, so the two will not agree digit for digit.

> **Known consequence of the specified constants.** The offset (101) and the
> quadrant origin (100) differ by exactly one standard-deviation unit, so the
> formula's neutral value does not coincide with the origin: a sector sitting
> precisely at its own trailing mean relative strength scores 101 and reads as
> marginally *Leading* rather than dead centre. In practice a sector must fall
> more than 1σ below its trailing mean before an axis registers it as Lagging.
> This biases every point up and to the right. Both numbers are in `config.yaml`
> (`rrg.offset`, `rrg.origin`); setting `offset: 100.0` makes neutral and origin
> coincide, at the cost of departing from the specified formula. A test pins the
> current behaviour so changing either constant is a deliberate decision.

**Warm-up:** `RS_Momentum` normalises `RS_Ratio`, so the RRG needs
`2 × window − 1` = **119 bars** before its first plotted point exists.

---

## Data layer

`PriceProvider` (`backend/data/provider.py`) is a Protocol. Two implementations:

* **`YFinanceProvider`** — default. `auto_adjust=True`, so OHLC is adjusted for
  splits *and* dividends. Unadjusted history shows every dividend ex-date as a
  gap the channel logic cannot distinguish from a breakdown.
* **`SchwabProvider`** — `/marketdata/v1/pricehistory`, OAuth2 refresh-token flow.

### Ingest

* 5-year backfill on first run; incremental thereafter.
* Idempotent upsert keyed on `(symbol, date)` — a natural composite primary key,
  not a surrogate id. The daily job *will* be re-run by hand, and a system that
  double-counts bars on a retry is a system nobody can safely retry.
* **Adjustment-factor detection.** Incremental fetches overlap the stored
  history. If historical closes have moved by more than `adjustment_rtol`, a
  split or dividend has restated the series and the symbol is **fully re-pulled**
  rather than patched at the tail. Patching would leave a series that is
  unadjusted before the split and adjusted after it — a discontinuity that reads
  as a genuine breakout.

### Validation and quarantine

A symbol failing any **violation** is quarantined: not written to `bars`, and no
signals computed for it that run. Previously-stored good history is untouched,
and the run is recorded as `PARTIAL` with `data_quality: DEGRADED`.

| Check | Class |
|---|---|
| NaN in any OHLCV field | violation |
| `high < low` | violation |
| close outside its own high/low | violation |
| negative volume | violation |
| non-positive price | violation |
| duplicate or unsorted dates | violation |
| gap > 5 trading days | violation |
| **open outside its own high/low** | **warning** |

The last row is deliberate and worth explaining. Real Yahoo data reports a stale
open on in-progress sessions — observed on RSPM, RSPG, RSPN and RSPR — and
adjusted prices are floating-point products of an adjustment factor, so a bar
where `close == low` can land ~1e-16 outside its own range. **No engine function
reads the open**: ATR uses high/low/previous close, the channel uses
high/low/close. Quarantining four of the eleven equal-weight twins — and with
them the NARROW badge for those sectors — over a field nothing consumes would
trade away real information for no protection at all. The envelope check
therefore carries a relative tolerance, an out-of-range *close* remains fatal,
and an out-of-range *open* is recorded as a warning in `run_log.warnings`.
A test asserts the engine still never reads the open; if that changes, the
warning must be promoted back to a violation.

---

## Schema

| Table | Key | Contents |
|---|---|---|
| `bars` | (symbol, date) | adjusted OHLCV |
| `signals` | (symbol, date, plane) | term structure (JSONB), RVOL, beta, breadth, NARROW |
| `states` | (symbol, date, plane) | state, `bars_in_state`, `entered`, `from_state` |
| `rrg` | (symbol, date) | RS, RS_Ratio, RS_Momentum, quadrant |
| `regime` | date | dispersion, percentile, correlation, benchmark state |
| `run_log` | run_id | status, `as_of`, `data_quality`, quarantined, warnings |

`rrg` is a sixth table beyond the five named in the specification: RRG
coordinates are per `(symbol, date)` but are **not** plane-specific, so folding
them into `signals` would mean duplicating every row across both planes or
leaving half of them NULL.

Per-horizon values live in `signals.term` as JSON keyed by horizon, so changing
`engine.channel.horizons` needs no migration — and it is structurally impossible
to add a "composite" column by accident.

---

## API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | status, database reachability, latest run |
| `GET /api/regime` | dispersion, correlation + sparkline, SPY state |
| `GET /api/sectors` | current state, both planes, all 3 horizons |
| `GET /api/sectors/{symbol}/history?days=250&plane=` | full signal time series + transitions |
| `GET /api/rrg` | coordinates + 10-day tails for all 11 |
| `GET /api/leaderboard?plane=relative` | sorted by `z_up` |
| `POST /api/refresh` | triggers ingest + recompute, returns `run_id` |
| `GET /api/runs/{run_id}` | job status |

**Every payload carries `as_of` and `data_quality`.** A number on a trading
dashboard without a date is not information.

**A partially computed day is never served.** `as_of` comes from the latest run
that actually completed — a crashed or half-finished refresh leaves the
dashboard showing yesterday rather than an inconsistent today. Before any run has
completed, the data endpoints return `503` and the UI shows an empty state.

`data_quality` is one of `OK`, `WARMING_UP` (insufficient history for every
statistic), `DEGRADED` (a symbol was quarantined), `STALE` (the latest complete
day is more than a working week old).

---

## Scheduling

One APScheduler cron job, weekdays at 18:30 America/New_York (in `config.yaml`).
`coalesce=True` and `max_instances=1` mean a missed day produces one catch-up
run, never a burst, and two refreshes can never overlap.

That is the entire scheduling layer, deliberately. The workload is 23 symbols
once a day; a message bus or streaming framework here would be architecture for
its own sake and would add failure modes a single cron-shaped job does not have.

---

## Testing

```bash
.venv/bin/python -m pytest                          # backend
.venv/bin/python -m pytest --cov=backend/engine     # with coverage
cd frontend && npm test                             # frontend
```

The suite that matters:

1. **No-look-ahead** (`test_no_lookahead.py`) — computes every signal on the full
   series, truncates the input to `T−k` for k ∈ {1, 5, 20, 60}, recomputes, and
   asserts every historical value is **bit-identical**. Not approximately equal:
   these are the same operations on the same float64 inputs in the same order, so
   any tolerance would only hide a real leak. Covers every component and the full
   pipeline, both planes, all 11 sectors.
2. **Synthetic series** (`test_synthetic.py`) — step, ramp, sine and random walk,
   each with an analytically derivable answer.
3. **ATR reference** (`test_atr.py`) — hand-computed against a 20-bar integer
   fixture, with the derivation written out in the module docstring.
4. **Beta/residual** (`test_beta.py`) — feeds `r_i = 1.5·r_m + noise`, asserts
   recovered β ≈ 1.5 and that the residual's correlation with the market falls
   below a 3σ bound, while the naive ratio does not.
5. **State machine properties** (`test_state_machine.py`) — Hypothesis-based: no
   transition is ever illegal or skipped, no oscillation within a bar,
   `bars_in_state` increases monotonically within a state.
6. **Data validation** (`test_validation.py`, `test_ingest.py`) — malformed
   inputs are quarantined, quarantine is isolated per symbol, and a split
   triggers a full re-pull.

### A note on "zero confirmed breakouts on a random walk"

The acceptance criterion asks for exactly zero confirmed breakouts on a seeded
1000-bar random walk. Taken literally and unconditionally, **that is unachievable
by any Donchian-channel detector**, and it is worth being precise about why:

* A driftless random walk makes new 20-day highs roughly 25–30 times per 1000
  bars. That is a property of random walks. A system that never reached PENDING
  on a random walk would never reach PENDING on a real trend either.
* Confirmation additionally requires `RVOL > 1.3`. With realistic i.i.d. volume
  about 19% of bars clear that gate by chance, so a handful of coincidences per
  1000 bars is arithmetically forced.

It is therefore tested in the two forms that are both achievable and meaningful:

1. **Exactly zero** when the walk has no volume surge. With flat volume RVOL is
   identically 1.0, the gate can never open, and the count is a hard zero across
   40 seeds. This is the test that fails loudly if anyone deletes the RVOL gate.
2. **Rare and directionally unbiased** with realistic volume, at the 3σ tolerance
   the specification names: fewer than one confirmation per 1000 bars on average,
   and upside/downside counts balanced within binomial sampling error. A
   look-ahead leak or a sign error shows up here as an inflated or lopsided count.

The null uses an **arithmetic** random walk. A geometric walk that is driftless
in log space still has positive drift in price levels, and the Donchian channel
measures price levels — so a geometric walk produces a genuine structural upside
skew that would fail a symmetry assertion for reasons unrelated to the code. A
separate test documents that skew rather than hiding it.

---

## Troubleshooting

**`ERROR: Package 'sector-breakout' requires a different Python: 3.9.6 not in '>=3.11'`**

A bare `pip` resolved to a Python outside the virtualenv. macOS ships
`/usr/bin/pip3` bound to Python 3.9, and a past `pip3 install --user` leaves a
`~/Library/Python/3.9/bin` on `PATH` that can win over an activated venv —
especially when conda's `base` is activated *after* the venv, since conda
prepends its own `bin` to `PATH`.

Check what you actually have:

```bash
which -a pip pip3 python3 && python3 -c "import sys; print(sys.executable, sys.version)"
```

The fix is to never rely on a bare `pip`. Call the interpreter you mean:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

`python -m pip` always installs into the interpreter that ran it, so it cannot
be shadowed by a stray script on `PATH`.

**Verified dependency versions.** The suite and a live 23-symbol backfill were
run against both `pandas 2.3.3 / numpy 2.3.5 / pytest 8.4` and
`pandas 3.0.5 / numpy 2.5.2 / pytest 9.1`, producing byte-identical signal
output. The floors in `pyproject.toml` are therefore left open rather than
pinned, but pandas 3.0 is a major release — if you upgrade further, re-run
`test_no_lookahead.py` first, since it is the test that would catch a silent
change in rolling/EWM semantics.

---

## Non-goals

No order execution, broker integration, backtesting/PnL attribution, user auth,
multi-user support, intraday/streaming updates, ML models, or alerting. This is a
read-only daily-bar analytical dashboard.

There is deliberately no Kafka, Spark, Redis, Docker Compose orchestration, or
message bus. The workload is 23 symbols × 1 update/day.
