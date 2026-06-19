# Farm Loop - Continuous Research Lifecycle

Status: **ACTIVE**. Last updated: 2026-06-19. Branch: `feature/calc-farm`.

This is the canonical calculation-farm lifecycle. It supersedes the older
`universe_farm_loop` and `scanner_farm_loop` operator paths. Everything here is
paper/research only: no `.env`, no `AUTO_TRADE`, no order execution, no private exchange
endpoints, no Telegram.

## What It Is

`scripts/strategy_lab/farm_loop.py` drives
`research_lab.farm_coordinator.run_coordinator_cycle`. Each pass picks the next
meaningful step for each symbol/timeframe/family and records it as a typed lifecycle task
with a machine-readable reason. It does not spin forever on `already_queued`; saturated
work pivots to deferred-ready work, universe discovery, or `blocked:no_eligible_tasks`.

## Two Databases

| DB | Module | Role |
|----|--------|------|
| `state/farm_tasks.sqlite` | `farm_tasks_db.py` | Brain: typed lifecycle, scheduling, reasons, fingerprints. |
| `state/strategy_lab.sqlite` | `state_db.py` | Compute queue: the worker executes materialized sweep jobs. |

The brain decides what should happen. The compute queue only executes bounded jobs. A
`run_sweep` brain task materializes into `strategy_lab.sqlite` and links back through
`materialized_queue_job_id`.

## Task Types And States

Task types: `intake_event`, `resolve_instrument`, `prepare_data`, `enrich_funding`,
`enrich_oi`, `run_sweep`, `classify_result`, `export_validation`,
`run_or_refresh_validation`, `schedule_followup`.

States: `queued`, `running`, `deferred`, `blocked`, `completed`, `skipped`, `failed`.
Every non-running state carries a `machine_reason`: examples are `data_missing`,
`too_short`, `NEEDS_OI_DATA`, `NEEDS_FLOW_DATA`, `NEEDS_MICRO_DATA`, `data_ready`,
`gate_cleared`, `compute_completed`, `compute_deduped`.

## Re-Arm Rule

`run_sweep` task keys include symbol, timeframe, family, and `data_fingerprint`.
Identical active tasks are deduped. Identical recently completed tasks are not recreated
until TTL expires. Fresh candles or enrichment change the fingerprint, so the farm can
recompute on new data without being blocked forever by old completed work.

Data-gated families use one fingerprint-independent gate task until the missing gate
clears, for example `NEEDS_OI_DATA`.

## Cycle

1. **Intake:** scanner `watch_queue.jsonl` is read as a file through `intake_adapter`;
   the scanner module itself is not imported. OKX discovery can refill the universe.
2. **Plan:** `data_planner` decides prepare/defer/enrich/block/run_sweep per symbol and
   timeframe. Missing data becomes `prepare_data`; too-short fresh listings defer to a
   concrete time; OI/funding families request enrichment; microstructure remains blocked
   as `NEEDS_MICRO_DATA`.
3. **Unblock:** blocked sweeps return to `queued` when their data gate clears.
4. **Execute:** in apply mode, bounded prepare/enrich/sweep work runs. Successful prepare
   can re-plan into a sweep in the same cycle.
5. **Worker:** `--run-worker` drains a bounded number of compute jobs from
   `strategy_lab.sqlite`.
6. **Classify:** completed runs are read from `metrics.json`, written to
   `unique_candidates`, and exported for validation when eligible.
7. **Validation:** `--run-validation` exports candidates, runs the honest-backtest bridge
   in-process, stamps verdicts back into `farm_results` and `unique_candidates`, and
   writes `setup_library` cards. The handoff uses bounded stored trade records in
   `metrics.json`; legacy aggregate-only artifacts are rebuilt from local candles when
   possible, never guessed from averages.
8. **Follow-up:** hard-validation feedback is converted into bounded
   `schedule_followup` tasks. Queueable actions (`NARROW_PARAMS`, `WIDEN_PARAMS`,
   `REGIME_SWEEP`) become ordinary typed `run_sweep` tasks and then use the same
   worker path as every other calculation. Follow-ups are capped, deduped, TTL-bound,
   and can be disabled with `--no-followups`.
9. **Paper:** `--run-paper` reads only `PAPER_FORWARD_READY` setup cards, builds
   `PaperTradePlan`, simulates against local prepared candles, writes
   `paper/paper_trades.jsonl`, and upserts `paper_outcomes`. A setup card is paper-ready
   only if hard validation passed and executable params include `hold_bars`, `stop_pct`,
   `take_pct`, with percent-point units and at least 1:2 reward/risk.
10. **Derived setup lifecycle:** status/reporting rebuild positive, negative, mixed, and
   no-sample setup groups from canonical artifacts. These groups are research evidence,
   not promotion/deletion rules.
11. **Pivot:** the cycle reports `work_available`, `advanced_lifecycle`,
   `discovery_refill`, or `blocked:no_eligible_tasks`.

## Parameter Authority

`strategy_registry.py` remains the source of truth for strategy IDs, defaults,
timeframes, and required data. `configs/strategy_lab/param_schemas.yaml` is only a
validator overlay: it rejects unknown keys, invalid types/ranges, bad percent units, and
paper/LLM proposals whose `take_pct` is less than `2 * stop_pct`.

Internally generated farm sweeps may normalize old registry defaults into executable
1:2 exits before calculation. External/LLM proposals are not silently normalized; they are
rejected with explicit reason codes.

## Data Gates

- **Funding:** keyless public OKX funding-rate history, enabled by `--enrich-funding`.
- **Open interest:** keyless public OKX open-interest history, enabled by `--enrich-oi`.
  A merged `oi` field clears `NEEDS_OI_DATA`.
- **Microstructure:** no keyless public provider for `obi_top5`, `trade_delta_100`, or
  `spread_bps`; these families honestly remain `NEEDS_MICRO_DATA`.

## Logs

Structured logs live under `<private_root>/logs/farm/` and are rotated:

- `cycle_log.jsonl` - one row per cycle.
- `task_transitions.jsonl` - one row per state change.
- `errors.jsonl` - worker/cycle errors.

Operators should use `status` and `farm_status_report`; raw logs are audit material, not
the normal dashboard.

## Commands

```bash
# Plan only, writes nothing.
python -m scripts.strategy_lab.farm_loop --once --dry-run

# One compute cycle.
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding --enrich-oi

# One full compute + validation + paper cycle.
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --enrich-funding --enrich-oi

# Continuous full cycle.
python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --run-paper --enrich-funding --enrich-oi --sleep-seconds 180 --stop-file STOP

# Visible operator wrapper.
bat\strategy_lab_farm_full_cycle_loop.bat
bat\strategy_lab_farm_full_cycle_stop.bat

# Status.
python -m scripts.strategy_lab.status
python -m scripts.strategy_lab.farm_status_report
```

## Honest Limits

- `FAILED_COSTS` and `NEEDS_MORE_DATA` are not runtime failures. They mean validation
  refused to create a paper setup.
- Paper simulation does nothing until a setup card is actually `PAPER_FORWARD_READY`.
- Microstructure is blocked until a provider exists.
- Older `scanner_farm_loop`, `universe_farm_loop`, and `research_loop` remain available
  for diagnostics/history, but they are not the current farm core.
