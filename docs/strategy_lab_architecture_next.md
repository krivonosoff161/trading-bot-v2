# Strategy Lab — Next Architecture (foundation pass)

Date: 2026-06-13. Status: foundation modules built and tested, **not yet wired
into the worker/runner**. This document describes the direction and the pieces
that now exist; it makes no profitability claims.

## Why

The MVP 2.0 lab tests "one asset + one strategy + some parameters". That found
research labels and candidates, but it kept hitting one recurring pain: a setup
could have the **direction** right while the **entry** was late — final PnL alone
hid where the setup actually failed.

The next architecture reframes the unit of work from a single (asset, strategy)
pair to an **event** and the assets around it, and it measures entry timing
explicitly:

```text
event cluster
  -> related assets (universe relations)
  -> timeframe profile (role + limits)
  -> setup detection
  -> entry timing quality   <-- the old pain, now a first-class metric
  -> coarse sweep (spec only in this pass)
  -> reducer
  -> CPU deep validation
  -> LLM review pack (advisor only)
  -> proposal queue
```

This pass builds the left-hand foundation (universe, timeframe profiles, resource
policy, event clusters, entry timing) and the **spec/validation** for coarse
sweeps. It does **not** build the sweep executor, the reducer, or any GPU
backend.

## What exists now

### Configuration (public, research metadata only)

- `configs/strategy_lab/universe.yaml` — asset groups (`core_market`,
  `l2_high_beta`, `meme_flow`, `ai_equity_proxy`) and relation hints
  (`market_beta`, `risk_proxy`). Relations are "tends to move with" context, not
  causal claims and not signals. Same-group peers are derived automatically.
- `configs/strategy_lab/timeframe_profiles.yaml` — role and bounded limits per
  timeframe: `1d` regime, `1h` setup, `15m` entry, `1m` event microscope. Each
  profile caps `max_symbols_per_cycle` and `max_variants_per_setup`; `1m` is
  `trigger_only` with a `max_window_hours` bound.
- `configs/strategy_lab/resource_policy.yaml` — quiet-desktop defaults (one
  worker, bounded queue, throttled cadence, no heavy or full 1m jobs) plus an
  opt-in `night_mode` section that overrides only the keys it lists.

### Loaders (`src/research_lab/`)

- `config_io.py` — shared YAML loading + clear failure messages.
- `universe.py` — `load_universe()` -> `Universe` with `group_of`, `peers`,
  `related` (relation hints + peers, deduped, self excluded; lookups normalize
  symbol form and case).
- `timeframes.py` — `load_timeframe_profiles()` -> `TimeframeProfiles` with
  case-insensitive `get` (so `1D`/`1H` map onto the lowercase keys).
- `resource_policy.py` — `load_resource_policy(night_mode=False)` ->
  `ResourcePolicy` with `allows_1m()` and `with_night_mode()`.

### Event layer (`event_cluster.py`)

`EventCluster` labels one historical move (anchor, related symbols, timeframe,
move start/end index+ts, direction, move %, pre-windows, post-window, source
reason). `detect_move_events()` finds non-overlapping close-to-close moves above
a threshold. `attach_related()` fills related symbols from the universe.
`pre_move_candles()` returns bars **strictly before** the move start so any
pre-move feature built later cannot use look-ahead information.

### Entry timing (`entry_timing.py`)

`entry_timing_metrics()` returns deterministic entry-quality metrics for a
move/entry pair: `entry_lag_bars`, `missed_move_pct`, `capture_ratio`,
`max_favorable_excursion_pct`, `max_adverse_excursion_pct`, `entry_before_impulse`,
`false_early_entry`, plus a `zero_movement` flag. Entry price is the open of the
entry bar (matching the simulator). These are diagnostics, not profitability
claims.

### Coarse-sweep spec (`sweep_spec.py`)

`SweepSpec` is the public schema for a future sweep (anchor, related symbols,
timeframe, setup/entry/exit/filter grids, `max_variants`, `backend`,
`resource_class`, `private_output_policy`). `validate_sweep_spec()` enforces the
safety gates **before** anything could run:

- 1m / trigger-only timeframe requires `resource_class == event_1m` and a scope
  within `max_symbols_per_cycle` (so full-universe 1m sweeps are rejected);
- heavy jobs require `allow_heavy_jobs` (rejected under `quiet_desktop`);
- `max_variants` is clipped to the timeframe profile cap, and a variant grid that
  exceeds the effective cap is rejected;
- `private_output_policy` may not point inside the public repo.

There is **no sweep executor and no GPU backend** in this pass.

## Boundaries / honesty

- GPU is a **planned optional** batch backend. It is not implemented and the code
  does not claim acceleration.
- 1m is a **trigger-only event microscope**, gated in both the timeframe profile
  and the resource policy — never a full-universe scan by default.
- The LLM stays a **review/advisor** step. Nothing is auto-executed or
  auto-enqueued by a model.
- Validation and statuses are **research labels and candidates**, not
  profitability claims.
- Private results stay in the private `trading-bot-research/strategy-lab` root;
  the public repo holds method, schemas, and config only.
- No live trading, no order-engine changes, no `.env`/secrets touched.

## Not implemented yet (next steps)

- Wire the universe/timeframe/resource layers into the worker and autopilot so
  cadence, queue size, and 1m/heavy gating are enforced at runtime (currently the
  policy is loadable and shown on the dashboard, but the worker loop does not yet
  read it).
- Sweep executor + reducer that turn a validated `SweepSpec` into runs.
- An event-driven spec generator (event cluster -> candidate sweep specs).
- Optional GPU batch backend behind the `backend: gpu` field.
- A 1m data path for the event microscope (no live downloader added here).
