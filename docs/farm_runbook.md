# Farm Runbook — what is active now & how to operate it

Status: **ACTIVE** · Last updated: 2026-06-18

The short answer to "what runs the calculation farm now": **`farm_loop`** (the continuous
lifecycle, see [farm_loop_lifecycle.md](farm_loop_lifecycle.md)). Everything is
paper/research only — public OKX data, no `.env`, no `AUTO_TRADE`, no orders, no Telegram.

## What is active now

- **Core loop:** `python -m scripts.strategy_lab.farm_loop` (brain = `farm_tasks.sqlite`).
- **Compute executor:** `worker_once` / `worker_loop` drain `strategy_lab.sqlite` (the
  brain materializes `run_sweep` jobs into it; `--run-worker` drains a few per cycle).
- **Operator picture:** `farm_status_report` (terminal) and the cockpit dashboard.
- **Legacy loops** (`universe_farm_loop`, `scanner_farm_loop`, `research_loop`, …) still
  exist with assigned roles — see [farm_ownership_map.md](farm_ownership_map.md). The new
  `farm_loop` is the intended core; legacy loops are not yet removed.

## Daily operation

```
# 1. See what the farm WOULD do (writes nothing; in-memory task DB):
python -m scripts.strategy_lab.farm_loop --once --dry-run

# 2. Run one real cycle (fetch missing candles, enrich, queue+compute, classify):
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding --enrich-oi

# 3. Add honest validation (export -> honest-backtest -> stamp-back, in-process):
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation \
    --enrich-funding --enrich-oi

# 4. Continuous (quiet heartbeat; full block only on change/error):
python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation \
    --enrich-funding --enrich-oi --sleep-seconds 180 --stop-file STOP --quiet

# 5. Read state (never tail raw logs for status):
python -m scripts.strategy_lab.farm_status_report
python -m scripts.strategy_lab.farm_status_report --json
```

## Stop / restart

- **Stop a loop:** create the file named by `--stop-file` (e.g. `STOP`) — the loop exits
  cleanly after the current cycle — or press Ctrl+C.
- **Restart:** just run the command again. State lives in `state/farm_tasks.sqlite`
  (brain) and `state/strategy_lab.sqlite` (compute queue); both are persistent and
  idempotent, so a restart resumes without reprocessing or double-queuing.
- **Worker stuck:** `worker_once` auto-reaps stale `running` jobs; for manual recovery
  `python -m scripts.strategy_lab.requeue_stale_jobs`.

## Where things are written (private root)

`<private_root>` = `TRADING_BOT_RESEARCH_ROOT` (default `~/github_projects/trading-bot-research/strategy-lab`,
outside the public repo). Apply mode refuses to write inside the public repo unless
`--allow-public-output`.

- `market_data/<tf>/*.json` — prepared candles (+ `funding`/`oi` enrichment fields).
- `market_data/oi/<SYMBOL>_oi.{json,csv}` — optional manual OI slot (fallback).
- `state/farm_tasks.sqlite` — task lifecycle, intake events, unique candidates.
- `state/strategy_lab.sqlite` — compute queue, runs, candidates, farm_results.
- `plans/event_specs/*.json` — materialized sweep specs (bounded: newest 500 kept).
- `hard_validation/{requests,verdicts,reports}/` — validation artifacts.
- `logs/farm/{cycle_log,task_transitions,errors}.jsonl` — structured farm logs (rotated).

## Storage hygiene (bounded)

Each apply cycle runs `storage_policy.maintain` (rotates the farm logs) +
`bound_farm_artifacts` (caps `event_specs` to 500, prunes terminal tasks + unique
candidates to 5000). Market data lives on the configured private root (point
`TRADING_BOT_RESEARCH_ROOT` at the HDD to keep the SSD clear).

## Safety boundary (always)

No `.env`, no `AUTO_TRADE`, no order execution, no private exchange/account endpoints, no
Telegram. Public OKX market data only. The boundary is enforced by an AST import test over
the farm modules. See [farm_notification_layer.md](farm_notification_layer.md) for the
*future* (design-only) notification layer.
