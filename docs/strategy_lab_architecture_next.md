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

## Runtime integration (done 2026-06-13)

The resource policy and universe/timeframe layers are now wired into the runtime,
not only the schema:

- `src/research_lab/runtime_policy.py` enforces job cadence
  (`min_seconds_between_jobs`, `max_jobs_per_hour`) and a per-job variant cap
  (`max_variants_per_job`). `worker_once.py` defers with a clear message when the
  throttle blocks a run; `worker_loop.py` sleeps the policy interval by default.
  `night_mode` is opt-in only.
- `src/research_lab/research_plan.py` + `scripts/strategy_lab/enqueue_research_plan.py`
  generate bounded ExperimentSpec jobs from a universe group and timeframe
  (dry-run / apply, idempotent, missing-data reported as skipped).
- `outputs.py` keeps REJECT rows out of the candidate registry by default.
- `review_export.py` + `scripts/strategy_lab/export_llm_review_pack.py` export a
  summaries-only review pack with no API call.
- The dashboard shows worker/queue health (incl. deferred reasons) and the
  LLM-review enabled/disabled flag.

## MVP 3.0 research-farm path (done 2026-06-13)

The discovery loop now runs end to end on the CPU, quietly:

- `src/research_lab/sweep_compile.py` compiles a validated `SweepSpec` into one
  bounded `ExperimentSpec` (grids expanded then clipped to the timeframe/policy
  caps — never an unbounded cartesian explosion). The executor stays
  `evaluate_spec`; there is still no GPU backend.
- `src/research_lab/reducer.py` aggregates run variants by (family, symbol) into
  verdicts `REJECT / OBSERVE / REGIME_SPECIFIC / FORWARD_PAPER / NEEDS_MORE_DATA`
  with reason codes (`too_few_trades`, `unstable_parameters`, `regime_dependent`,
  `entry_late`, `drawdown_too_high`, `weak_edge`, `candidate_for_forward`). A
  single lucky parameter without neighbor support is never promoted. Each run now
  writes `reducer_report.json`.
- `src/research_lab/event_sweeps.py` + `scripts/strategy_lab/generate_event_sweeps.py`
  turn strong historical moves into bounded sweep proposals (dry-run default).
  The event is historical; sweeps run the normal no-lookahead simulator; 1m stays
  trigger-only, never a full brute force.
- Entry timing is first-class: `experiment.py` records per-trade MFE/MAE/capture
  and an `entry_timing` metrics block; the reducer uses it for `entry_late`; the
  review pack and dashboard surface the aggregate.
- `src/research_lab/obsidian_graph.py` + `scripts/strategy_lab/build_obsidian_graph.py`
  write one private Obsidian note per non-REJECT candidate (verdict, reasons,
  related symbols, linked run, next test).
- The dashboard adds a Research Summary card (latest reducer verdicts, entry
  timing aggregate, Obsidian note count, next-run/deferred reason).

## Closed proposal loop (done 2026-06-13)

results -> review pack -> proposal generation -> validation -> safe queueing ->
worker -> new results, all deterministic and dry-run by default:

- `src/research_lab/proposal_schema.py` + `proposal_store.py` — typed proposals
  (`PROPOSED -> VALIDATED / REJECTED -> QUEUED`) stored privately in
  `proposals/proposals.jsonl`.
- `src/research_lab/proposal_generator.py` + `scripts/strategy_lab/generate_next_proposals.py`
  — rule-based next-test proposals from the registry (never promotes; only
  requests the next bounded test).
- `src/research_lab/proposal_validator.py` — validates schema, resource caps,
  timeframe policy (1m full sweep blocked), known symbols/families, bounded
  variants, safe wording, and the private/public boundary.
- `scripts/strategy_lab/queue_validated_proposals.py` — compiles VALIDATED
  proposals to ExperimentSpec and queues them idempotently, bounded by
  `max_queue_size`. `import_llm_proposals.py` imports human-saved LLM output via
  a local file read (no API). The dashboard shows proposal counts and an explicit
  "LLM auto-send: disabled / queue requires apply" status.

This is separate from the older `proposals.py` autopilot (manual/advanced), which
uses `proposals/proposal_registry.jsonl` + `proposals/specs/`.

## MVP 4.0 (done 2026-06-13)

- **1m event-microscope data path** — `src/research_lab/event_microscope.py` +
  `scripts/strategy_lab/microscope_scan.py`. A read-only locator finds existing
  local 1m files, derives caps from the trigger-only 1m profile + resource policy
  (`max_symbols`, `max_event_windows`, `max_bars_per_window`, `max_variants`), and
  reports `available / missing / too_short / not_1m`. No downloader; missing data
  is a clean skip. Full-universe 1m sweeps remain blocked in `sweep_spec`.
- **Event-anchored entry timing in runs** — `entry_timing.event_anchored_timing()`
  plus an optional `event_context` on `ExperimentSpec`. When the event-driven
  sweep path queues a job it carries the historical move's ts window; `evaluate_spec`
  then measures the first trade's lag/capture/missed-move/MFE/MAE and a
  `late_entry` flag. No look-ahead: the simulator still decides entries; future
  bars are used only to measure the outcome. Without context, metrics are unchanged.
  The reducer flags `entry_late` from either the proxy or the event-anchored rate.
- **Controlled LLM send boundary** — `src/research_lab/llm_review_sender.py`: a
  `ReviewSender` protocol, a default `NullReviewSender` (never networks), pure
  `evaluate_send_gates`, and private `record_send_intent`. A send requires
  `--send`, not-dry-run, `STRATEGY_LAB_LLM_ENABLED=1`, a configured provider, and a
  daily budget cap that is not exhausted. Export stays the default.
- **Dashboard/status visibility** — `event_microscope`, enriched `llm_review`
  (provider/cap/last-pack/send gate), `queued_from_proposals`, and `queue_capacity`
  are surfaced as labels only.

## MVP 4.2 Phase 1 (done 2026-06-13)

Demand-driven 1m data loader — prepares 1m data only for active needs, capped and
safe by default:

- `src/research_lab/data_requirements.py` — derive capped 1m windows (UTC) from
  event specs / queued jobs (event_context) or an explicit manual request. Windows
  are clipped to `max_bars_per_window`; `apply_policy_caps` marks anything beyond
  `max_event_windows` / `max_symbols` as `outside_policy`.
- `src/research_lab/data_cache.py` — read-only cache check: present / missing /
  too_short / malformed by counting deduped bars inside the window. No download.
- `src/research_lab/market_data_provider.py` — `MarketDataProvider` protocol
  (market-data only), default `NullMarketDataProvider` (no network,
  `provider_not_configured`), and an offline deterministic `SyntheticMarketDataProvider`
  gated behind `STRATEGY_LAB_ALLOW_SYNTHETIC=1` for tests/demos. No real adapter ships.
- `src/research_lab/data_prepare.py` — canonical 1m writer (deduped, sorted, atomic,
  merge-safe) under `strategy-lab/market_data/1m/` (private root only), plus the
  `prepare()` orchestration and a private prepare-report.
- `scripts/strategy_lab/prepare_1m_data.py` + `bat\strategy_lab_prepare_1m_data.bat`
  — dry-run by default; `--apply` writes only with a configured provider, else
  `provider not configured / no data written`. `--max-symbols`/`--max-windows` only
  lower caps.
- The microscope locator + dashboard/status now read `strategy-lab/market_data/1m/`
  and surface the last prepare report (provider/mode/missing/downloaded, labels only).

## MVP 4.2 Phase 2 (done 2026-06-13)

Real public market-data adapter for the loader:

- `src/research_lab/providers/okx_public.py` — `OkxPublicMarketDataProvider`: a new,
  isolated, **public-only** adapter (the existing OKX clients mix order/account/stream
  code and were intentionally **not** reused). It calls only OKX
  `/api/v5/market/history-candles` via stdlib `urllib` (no new dependency), needs no
  API key, maps `BTC_USDT_SWAP -> BTC-USDT-SWAP`, paginates backward (`after` = oldest
  ts) only across the requested window with a bounded page count (<= 20 pages) and a
  short backoff, skips unconfirmed candles, and returns canonical OHLCV rows (deduped,
  sorted, UTC, window-filtered). Network/parse failures raise `MarketDataError`, which
  `prepare()` records as a structured `provider_error` (no crash, no partial write).
- `get_provider("okx-public")` wires it behind the existing interface; `null` stays the
  default and `synthetic` stays env-gated. `prepare_1m_data --provider okx-public`
  fetches only on `--apply` (dry-run makes no network call).

## MVP 4.2 Phase 3 (done 2026-06-13)

Operator-workflow integration of the prepare step — opt-in, never automatic:

- `src/research_lab/prepare_workflow.py` — `load_prepare_workflow_config()` reads the
  `STRATEGY_LAB_PREPARE_1M[_APPLY]` / `STRATEGY_LAB_MARKET_DATA_PROVIDER` /
  `..._MAX_SYMBOLS` / `..._MAX_WINDOWS` env flags with safe fallbacks + warnings, and
  exposes `mode` and `will_fetch_network`. Read-only; it never fetches or writes.
- `bat/strategy_lab_start.bat` gains an opt-in `[2c]` prepare step: disabled by
  default (no network), dry-run when `PREPARE_1M=1`/`APPLY=0`, apply only with
  `APPLY=1` + a real provider. A whole-start dry-run forces the step to dry-run.
- `status` and the dashboard surface the auto-prepare config (enabled/mode/provider/
  will_fetch_network) alongside the last prepare result; `microscope_scan` and
  `status` point to the exact `prepare_1m_data` commands when 1m data is missing.
- The worker still never fetches by itself; only the explicit start step or the
  manual CLI can.

## MVP 4.3 (done 2026-06-13)

Controlled event-driven research cycle — one bounded, operator-visible pass that
orchestrates existing steps without becoming autonomous:

- `src/research_lab/research_cycle.py` — JSON-friendly models (`ResearchCycleConfig`,
  `CycleStepResult`, `ResearchCycleResult`) + the private status-report writer/reader
  and a compact `cycle_summary`.
- `scripts/strategy_lab/research_cycle.py` — `run_research_cycle()` + CLI. Steps:
  inspect -> generate proposals -> check 1m data -> optional prepare -> queue (capped)
  -> one throttled worker step -> write `state/research_cycle/latest.json`. Dry-run by
  default (no queue writes, no worker, no network). A real fetch needs
  `--apply --prepare-1m --prepare-1m-apply --provider okx-public`. The worker step
  reuses `run_worker_once` and respects the cadence throttle; on `deferred` it records
  and stops — no storm, no hidden loop.
- `worker_once.py`, `generate_next_proposals.py`, `queue_validated_proposals.py` were
  refactored minimally to expose `run_worker_once()` / `generate_proposals()` /
  `queue_validated()` functions (CLIs preserved) so the cycle reuses them.
- `status` + dashboard surface the last cycle; `bat/strategy_lab_cycle_dry_run.bat`
  is dry-run only.

## MVP 4.4 (done 2026-06-13)

Data-complete research sessions + an advisory LLM proposal loop (reusing the existing
validator and send boundary, not duplicating them):

- `src/research_lab/strategy_requirements.py` — `StrategyDataRequirement` +
  `derive_requirement()`: reads the longest lookback from `strategy_registry`
  parameter_defaults and sets `min_rows = lookback + warmup + buffer`. Minimal and
  explicit (no speculative HTF/related-symbol fields).
- `src/research_lab/data_readiness.py` — `assess()` / `assess_proposal()`: a thin
  adapter over `inspect_file` / `choose_symbol_file` (+ `locate_microscope_data` only
  for event-anchored jobs) returning READY / MISSING_DATA / TOO_SHORT / MALFORMED /
  PARTIAL_CONTEXT / OUTSIDE_POLICY, with a suggested prepare command. No TA, no fetch.
- `queue_validated()` gains `require_data_ready` (the cycle/session pass True): a
  not-ready proposal is skipped with a reason, never queued. Cycle counts now include
  `ready_jobs` / `skipped_missing_data` / `skipped_too_short` / `skipped_malformed`.
- `run_research_cycle` wraps each step in try/except (proposal_error / data_check_error
  / prepare_error / queue_error / worker_failed) — a component failure is recorded, not
  fatal; the report write stays loud.
- `src/research_lab/llm_proposals.py` — cheap->chief loop built on existing pieces:
  `validate_llm_candidates()` reuses `coerce_proposal` + `validate_and_mark`; gates
  reuse `llm_review_sender` (`evaluate_send_gates`, `daily_cap`, `env_enabled`) plus a
  provider gate; `NullProposalSender` is the export-only default. No new sender stack,
  no paid call by default, LLM output never executed. Alibaba/Qwen documented, disabled.
- `scripts/strategy_lab/research_session.py` + `src/research_lab/research_session.py` —
  a single-pass session CLI that wraps `run_research_cycle`, adds the LLM export/send
  step (gated), and writes one session report. status + dashboard surface the session,
  readiness counts and the LLM-loop mode.

## Not implemented yet (next steps)

- Optional GPU batch backend behind the `backend: gpu` field (CPU only today).
- More market-data providers / timeframes (today `okx-public` does 1m only). A
  full-market 1m download stays intentionally unsupported.
- A **real LLM provider** behind both the review-send and proposal-loop gates (only the
  null/export-only path ships; Alibaba/Qwen are documented but disabled).
- Event-anchored entry-timing inside *generic* (non-event) runs (those still use
  the per-trade MFE/MAE/capture proxy).
