# Strategy Research Lab — MVP 2.0

Date: 2026-06-12. Status: built, tested, local-only.

The lab is a private/local strategy discovery machine. It is NOT live trading
and produces NO profitability claims. Public code shows the method; the private
research workspace stores the actual results.

## Chain

```text
data inventory
  -> strategy registry (12 deterministic strategies)
  -> experiment queue (SQLite)
  -> worker (one job per pass)
  -> simulation (fees + slippage, stop/take/time exits)
  -> regime labeling (volatility / trend / volume, no lookahead)
  -> validator-lite (REJECT / OBSERVE / REGIME_SPECIFIC / FORWARD_PAPER)
  -> candidate registry (private JSONL, append/update safe)
  -> deterministic proposal generator
  -> private artifacts (metrics, candidates, summary, graph edges)
  -> SQLite state DB (runs / candidates / queue index)
  -> read-only dashboard (127.0.0.1)
  -> Obsidian notes / graph_edges.csv
  -> LLM review pack (prepared only; nothing is auto-executed or auto-enqueued)
```

## One-command start

```bash
bat\strategy_lab_start.bat
```

Syncs the state DB, queues a bounded research plan from `universe + timeframe`
(default: `core_market / 1d`), starts the dashboard (`http://127.0.0.1:8765`)
and the one-worker loop. The worker is throttled by
`configs/strategy_lab/resource_policy.yaml`.

Optional operator overrides:

```bash
set STRATEGY_LAB_UNIVERSE=l2_high_beta
set STRATEGY_LAB_TIMEFRAME=15m
set STRATEGY_LAB_FULL=1
set STRATEGY_LAB_NIGHT_MODE=1
```

## Starter research pack

`configs/strategy_lab/starter/` is the first always-on pack. It queues multiple
small specs instead of a single smoke job:

- majors / regime baselines;
- volatile L2 alts and meme-like assets;
- down/sideways regime checks for fades and retests;
- AI / equity proxy symbols.

Queue it directly:

```bash
python scripts/strategy_lab/enqueue_pack.py --dir configs/strategy_lab/starter --priority 50
```

The worker still processes one job at a time. This keeps the desktop safe while
allowing a richer 24/7 queue.

## Deterministic autopilot proposals (manual / advanced)

The lab can create the next small batch of experiment specs from its private
candidate registry:

```bash
python scripts/strategy_lab/autopilot_once.py --max-proposals 8 --priority 70
```

The proposal generator is intentionally conservative:

- `FORWARD_PAPER` and `OBSERVE` candidates become parameter-neighborhood sweeps.
- `REGIME_SPECIFIC` candidates become reruns with the strong regime bucket as a
  filter.
- proposal specs and the proposal registry are written only under the private
  `trading-bot-research/strategy-lab/proposals/` root by default.
- no LLM is called, no live order path is touched, and no private result table is
  written into the public repository.

This remains available for manual follow-up generation from the private
candidate registry. The default one-command start now uses
`enqueue_research_plan` so the active universe, timeframe, and resource-policy
limits are explicit. LLM review remains a prepared pack, not an automatic
decision maker.

## Manual chain

```bash
python scripts/strategy_lab/build_data_inventory.py --spec configs/strategy_lab/l2_smoke.json
python scripts/strategy_lab/sync_state_db.py
python scripts/strategy_lab/enqueue_experiment.py --spec configs/strategy_lab/l2_smoke.json --priority 50 --ensure
python scripts/strategy_lab/autopilot_once.py --max-proposals 8 --priority 70
python scripts/strategy_lab/worker_once.py
```

## Data inventory

`src/research_lab/data_inventory.py` scans the spec's `data_glob` and reports
per file: symbol, rows, start/end, inferred timeframe, has_ohlcv, and
quality_status (`usable` / `too_short` / `malformed` / `missing`). Spec symbols
without a usable file are flagged `missing` — this catches dead symbols before
a run silently skips them. Output goes to the private
`strategy-lab/inventory/`; public output is console counts only.

## Strategy registry

`src/research_lab/strategy_registry.py` + `src/research_lab/strategies/`.
Each entry: strategy_id, display name, family, description, compatible asset
classes/timeframes, parameter defaults, risk notes, and a deterministic
stdlib-only `generate_signals(candles, params)`.

Families: breakout (momentum_breakout, donchian_breakout, range_breakout,
volatility_squeeze_breakout, breakout_retest), mean_reversion
(mean_reversion_fade, rsi_reversal, volume_exhaustion_fade), trend
(trend_pullback, moving_average_reclaim), volume (volume_shock_continuation,
impulse_continuation).

Signal contract: decision on bar `idx-1`, entry at the open of bar `idx`.

## Regime layer

`src/research_lab/regime.py` labels each trade entry with volatility
(low/medium/high), trend (up/down/sideways), and volume (thin/normal/elevated)
buckets computed from bars strictly before entry. Specs may gate signals:

```json
"filters": {"volatility": ["medium", "high"], "trend": ["up", "down"]}
```

Omitted filters mean "run all". Regime labels are metadata and gating, not a
profitability claim. Default thresholds are tuned for 1D crypto-perp candles
and can be overridden per spec via `regime_params`.

`SweepSpec.filter_grid` compiles into `ExperimentSpec.filters`; it is a regime
gate, not a parameter-grid axis. Therefore it does not multiply
`variant_count`. Feedback-driven `REGIME_SWEEP` uses the candidate's stored
strong regime bucket when available and queues only bounded research sweeps.

## Validator-lite

`src/research_lab/validator.py`. The grading decision
`PROMOTE_FOR_PRESSURE_TEST` only means "worth validating". The validator then
assigns:

- `REJECT` — too few trades, or hard OOS/average failure with no carrying regime;
- `OBSERVE` — alive but soft failures (weak PF, dominance, inconsistency, cost stress);
- `REGIME_SPECIFIC` — overall weak, but one labeled regime bucket carries the
  result (next step: re-run with that regime as a spec filter);
- `FORWARD_PAPER` — passed all lite gates; next step is paper-forward tracking
  only. Not a profitability claim.

Checks: min trades, positive OOS, profit factor, single-trade dominance,
drawdown vs total, train/test consistency, +50% cost stress, and a parameter
fragility placeholder (neighbor sweeps are MVP 3.0).

## Candidate registry (private)

`strategy-lab/candidate-registry/candidates.jsonl` — one entry per
(experiment, candidate): params, metrics summary, decision, validation status
and reasons, regime metadata, artifact label, created_at and next_review.
Upserts are idempotent: repeated smoke runs update entries in place and
preserve `created_at`. The dashboard shows only counts; entries stay private.

## Dashboard

Read-only, binds 127.0.0.1 only, rejects POST, validates Host header. Shows
runs, queue, decision counts, validation counts
(REJECT / OBSERVE / REGIME_SPECIFIC / FORWARD_PAPER), top candidates, candidate
registry summary, and path labels only (never absolute private paths, never
`.env` values). Old runs imported before MVP 2.0 show as UNKNOWN validation.

## LLM review pack (prepared, not executed)

Each run writes `llm_review_pack.json` (schema v1: decision and validation
counts, family aggregates, regime metadata, top results) and
`llm_review_prompt.md`. The prompt instructs the LLM to hunt overfit, propose
next experiment specs as JSON drafts, and forbids profitability claims.
Nothing the LLM produces is auto-enqueued; a human reviews drafts and runs
`enqueue_experiment.py` manually.

## Public / private boundary

Public repo: code, schemas, spec examples, this doc. Private root
(`%USERPROFILE%\github_projects\trading-bot-research\strategy-lab`, overridable
via `TRADING_BOT_RESEARCH_ROOT`): all run artifacts, candidate registry,
inventory reports, Obsidian vault, SQLite state. No exact private result
tables or parameter findings belong in public docs.

## Tests

```bash
python -m pytest tests/test_research_lab_experiment.py tests/test_research_lab_state_db.py tests/test_research_lab_dashboard.py tests/test_research_lab_data_inventory.py tests/test_research_lab_strategy_registry.py tests/test_research_lab_regime.py tests/test_research_lab_validator.py tests/test_research_lab_candidate_registry.py tests/test_research_lab_proposals.py -q
python -m ruff check src/research_lab scripts/strategy_lab
```

## Next-architecture foundation (added 2026-06-13)

A foundation pass toward an event-driven research machine, now wired into the
runtime (see the runtime note below and the "Runtime integration" section of the
architecture doc). The building blocks are
[strategy_lab_architecture_next.md](strategy_lab_architecture_next.md). In short:

- `configs/strategy_lab/universe.yaml` + `src/research_lab/universe.py` — asset
  groups and relation hints (research metadata, not trading advice).
- `configs/strategy_lab/timeframe_profiles.yaml` + `src/research_lab/timeframes.py`
  — per-timeframe role and bounded limits; 1m is trigger-only.
- `configs/strategy_lab/resource_policy.yaml` + `src/research_lab/resource_policy.py`
  — quiet-desktop limits with an opt-in `night_mode`.
- `src/research_lab/event_cluster.py` — detect a historical move and attach
  related symbols (with a no-lookahead pre-move slice helper).
- `src/research_lab/entry_timing.py` — honest entry-quality metrics (lag, missed
  move, capture ratio, MAE/MFE, false-early-entry).
- `src/research_lab/sweep_spec.py` — a validated coarse-sweep schema that gates
  1m/heavy jobs and a public private-output path.

The dashboard now shows a read-only "Research Machine Config" card (universe
group/symbol counts, timeframe profiles, resource mode, proposal-spec count) and
a "Worker & Queue Health" card (queue states, last worker status incl. deferred
reasons, LLM-review enabled/disabled).

Runtime now enforces the resource policy: the worker throttles
(`min_seconds_between_jobs` / `max_jobs_per_hour`) and caps per-job variants
(`max_variants_per_job`), `night_mode` is opt-in, jobs can be generated from a
universe group + timeframe via `enqueue_research_plan` (dry-run / apply), REJECT
rows are kept out of the candidate registry by default, and an export-only LLM
review pack can be produced with no API call. Operator how-to:
[strategy_lab_operator_guide.md](strategy_lab_operator_guide.md). GPU remains a
planned optional backend, not implemented.

## Known limits (MVP 3.0 candidates)

- Parameter fragility is still a lite check; neighbor sweeps exist as bounded
  follow-up proposals, not as a full statistical robustness surface.
- Single-TP/SL bar-close simulation; no intra-bar ordering beyond stop-first scan.
- Regime thresholds are fixed defaults (1D-tuned), not data-adaptive.
- HTTP handler itself is untested (render/state layers are); localhost-only.
- LLM review execution (sending the pack to a model) is intentionally absent.
