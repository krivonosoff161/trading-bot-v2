# Calculation Farm — Phase 2 (operator guide + status, 2026-06-18)

Branch `feature/calc-farm`. Phase 2 turned the farm foundation into an operator-facing
machine across 9 stages. Paper/research only — no order path, `.env`, `AUTO_TRADE`,
live engine, private OKX/account/order endpoints, or Telegram were touched.
`main_engine_ready` stays `False` everywhere.

## What each stage added (with evidence)

1. **Migration-safe status report** (`25dcd22`): `farm_status_report` runs `init_db`
   before reading (old v2/v3 DBs are upgraded, not crashed), dedupes
   ready-for-validation by (symbol, family, timeframe), adds schema version, latest
   run, queue age, unique count, flow-coverage slot. Tests: old-DB no-crash, dedup,
   JSON stability.
2. **GPU rolling-window kernels** (`237825c`): vectorized numpy/cupy kernels for
   `range_volume_breakout` and `pump_dump_scalp`, **exact scalar↔vectorized parity**
   on deterministic fixtures (incl. real-GPU parity on the GTX 1050). Verified live:
   `signal_backend=gpu`, `accelerated_runs>0`. `volatility_squeeze_breakout_v2`
   deliberately stays CPU (ATR-percentile is a sequential recurrence — documented).
3. **Auto funding enrichment** (`0281d7b`): `flow_enrich` runs bounded public-OKX
   funding enrichment per cycle (state per (symbol,tf,file): enriched/no_points/
   fetch_failed; success TTL; failure backoff; per-cycle cap). `--enrich-funding`/
   `--max-flow-enrich-per-cycle`/`--flow-provider`; dry-run never calls the provider.
   Also fixed `load_candles` to preserve flow fields so the worker sees enriched data.
4. **OI slot + NEEDS_OI_DATA + quadrant A/B** (`9d3163a`): OI is never faked — a
   recorded JSON/CSV slot (`market_data/oi/<symbol>_oi.*`) with schema validation +
   no-lookahead merge (`enrich_oi_data`). Families that need oi/funding/microstructure
   now classify NEEDS_OI_DATA / NEEDS_FLOW_DATA / NEEDS_MICRO_DATA instead of being
   presented active. The OI-quadrant conflict is two explicit tested families:
   `oi_price_quadrant_continuation` (A) vs `oi_price_quadrant_trap_fade` (B) — proven
   to take opposite sides on the same quadrant.
5. **OKX instrument discovery** (`ec68622`): public instruments → live USDT-perp
   filter → confident classification (ambiguous → unknown, never guessed) → snapshot +
   TTL + diff (new/delisted/group-change) → `discovered_<group>` universe the loop
   grinds bounded by resource policy + caps. Verified live: **367 OKX perps classified**.
6. **Result classification + validation handoff** (`312a772`): schema v4 (additive
   ALTER) adds `max_drawdown_pct`, `gpu_signal_supported`, `hard_status`,
   `validation_exported` to `farm_results`. `validation_handoff` reads the
   hard_validation requests/verdicts back by `candidate_id == run_id` (exact join) and
   `validation_state` derives VALIDATION_EXPORTED/PASSED/FAILED/NEEDS_MORE_DATA.
7. **Dashboard cockpit** (`0164629`): `farm_cockpit` adds a read-only operator view
   (loop state, data readiness, GPU/CPU split, results + handoff, manual-vs-discovered
   universe coverage) to the existing localhost JSON dashboard. Defensive on old DBs;
   labels only, no secrets/absolute paths.
8. **Obsidian farm memory** (`3fe8b49`): `farm_obsidian` writes deterministic
   daily/symbol/family/candidate notes with graph wikilinks and data tags from
   farm_results + the registry (exact params on candidate notes).
9. **E2E** (real OKX): refill → prepare (3 candles) → **funding enrich (3, real)** →
   queue → worker (sim on GPU) → classify (OBSERVE 1 / REJECT 8) → status v4 →
   obsidian (5 candidate notes). 129 targeted tests green; ruff clean; git diff --check
   clean.

## How to run (operator)

```bash
export TRADING_BOT_RESEARCH_ROOT=<private dir outside the repo>

# 1) (optional) discover the live OKX universe -> snapshot
python -m scripts.strategy_lab.discover_okx_universe --apply

# 2) grind the universe: prepare candles, enrich funding, compute on GPU, drain worker
python -m scripts.strategy_lab.universe_farm_loop --once --apply \
  --groups core_market --timeframes 1h --units-per-cycle 1 \
  --max-prepares-per-cycle 3 --run-worker --max-worker-jobs-per-cycle 1 \
  --backend auto --enrich-funding --max-flow-enrich-per-cycle 3
#   ...or the discovered universe: add --discover-okx-universe --groups discovered_crypto_alt

# 3) operator status
python -m scripts.strategy_lab.farm_status_report          # or --json (dashboard-shaped)

# 4) export validated candidates -> hard validation, then read verdicts back
python -m scripts.strategy_lab.export_hard_validation_requests --apply --include-regime-specific
python -m scripts.strategy_lab.refresh_validation_handoff

# 5) farm-memory notes (Obsidian)
python -m scripts.strategy_lab.write_farm_obsidian

# OI (recorded slot, no faking): drop market_data/oi/<symbol>_oi.{json,csv} then
python -m scripts.strategy_lab.enrich_oi_data --symbol BTC_USDT_SWAP --timeframe 1h --apply
```

## Still partial / not done (honest)

- **GPU signal kernels: 3/13 families** (`momentum_breakout`, `range_volume_breakout`,
  `pump_dump_scalp`). The rest run signals on CPU (simulation still runs on GPU). Next:
  a kernel for a non-recurrent family if a parity-safe one remains; `squeeze_v2` is
  intentionally excluded (sequential ATR-percentile).
- **OI data is a recorded slot, not a live feed**: keyless per-instId OI history is not
  reliably public, so OI families are NEEDS_OI_DATA until the slot is populated.
- **Discovery is by classification, not volume-ranked movers**: coverage is systematic
  (cursor + caps), not liquidity-prioritized. A `/market/tickers` movers rank is a
  clean next add.
- **Dashboard cockpit is data, not a polished UI**: it ships the JSON fields; the HTML
  view is the existing dashboard server.
- **Long unattended `--full` run** to accumulate real candidates is the operator's call.
- **VALIDATION_PASSED/FAILED populate only after** export + honest-backtest + the
  handoff refresh are run (the chain is wired and tested, not yet accumulated).
