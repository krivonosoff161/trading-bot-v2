# Continuous scanner → farm loop

> **⚠️ SUPERSEDED (2026-06-18).** This describes the legacy `scanner_farm_loop` — a flat
> watch→queue coordinator with no typed-task brain. It is now one of two archive-legacy
> loops behind the current core, **`farm_loop`** (the continuous lifecycle with
> `farm_tasks.sqlite`, defer/block/unblock, classify, validation, and an anti-saturation
> pivot). Read [../farm_loop_lifecycle.md](../farm_loop_lifecycle.md) and
> [../farm_ownership_map.md](../farm_ownership_map.md). Kept for history; the
> `scanner_farm_pipeline._ensure_local_data` helper it documents is still reused by the
> coordinator.
>
> **Everything below describes LEGACY behavior. All commands here (`scanner_farm_loop`,
> `scanner_farm_loop.sqlite`) are legacy / off-default — do NOT use them for the current
> continuous research path; use `python -m scripts.strategy_lab.farm_loop` instead.**

A single automated coordinator that closes the gap between a scanner WATCH/GO and a
real farm calculation. It is **not** "run for N hours and read logs" — it runs in
cycles, checkpoints its progress, and reports a clear per-cycle status.

```
scanner watch_queue (enriched: okx_inst, selected_timeframe, farm_eligible)
  └─ scanner_farm_loop
       1. read fresh enriched watches (checkpointed — no reprocessing on restart)
       2. plan prioritized, deduped, eligible jobs (farm_scheduler)
       3. AUTO-materialize candles for each job → private market_data/<tf>/
       4. compile a bounded SweepSpec at the SELECTED timeframe, queue it
       5. (optional) drain a few farm jobs (worker_once) — feedback
       6. persist checkpoint + dedup + skip reasons + counters (sqlite)
```

## What it does each cycle

- Reads open scanner watches (`logs/scout/watch_queue.jsonl`), skipping any already
  processed (checkpoint in the state DB).
- Plans jobs by priority: **1** scanner WATCH/GO, **2** OKX announcement, **3** OKX
  market mover, **4** universe/backlog refill to keep the GPU busy.
- For every eligible job it **prepares the candles itself** (public OKX candles →
  `market_data/<timeframe>/`); preparation is part of the loop, never a manual step.
- Queues a bounded sweep against the **private prepared-root glob** at the timeframe
  the scanner selected by data (never a blind `1d`).
- Never duplicates a `symbol+timeframe+family` job, and never re-queues a
  pending/running/completed one (state-DB checkpoint + the farm queue's own
  idempotent `ensure_experiment_queued`).

## Commands

```bash
# one cycle, plan only (writes nothing)
python -m scripts.strategy_lab.scanner_farm_loop --once --dry-run

# one cycle, real: prepare candles + queue sweeps + write state
python -m scripts.strategy_lab.scanner_farm_loop --once --apply

# continuous (every 15 min), also drain up to 2 farm jobs/cycle, keep GPU busy
python -m scripts.strategy_lab.scanner_farm_loop --loop --apply \
    --sleep-seconds 900 --run-worker --max-worker-jobs-per-cycle 2 \
    --refill-universe core_market

# also run a scanner pass at the start of each cycle (opt-in; uses public news)
python -m scripts.strategy_lab.scanner_farm_loop --loop --apply --run-scanner-pass
```

### Run modes / flags

| Flag | Meaning |
|---|---|
| `--dry-run` / `--apply` | plan only / actually prepare+queue+write state (dry default) |
| `--once` / `--loop` | one cycle then exit / run continuously (default once) |
| `--sleep-seconds` | gap between loop cycles (default 900) |
| `--max-jobs-per-cycle` | hard cap on jobs queued per cycle (default 8) |
| `--max-data-prepares-per-cycle` | hard cap on candle preparations per cycle (default 8) |
| `--run-worker` + `--max-worker-jobs-per-cycle` | drain N queued farm jobs each cycle |
| `--run-scanner-pass` + `--scanner-limit` | run one scanner pass at cycle start |
| `--include-expired` | also consider expired watches (backlog) |
| `--refill-universe <group>` | top up with a universe group (`core_market`, `l2_high_beta`, `meme_flow`, `ai_equity_proxy`) |
| `--backend` | `cpu` / `gpu` / `auto` for the sweep backend |
| `--stop-file <path>` | loop exits cleanly when this file appears |

## Where things live

- **State / checkpoint:** `<private_root>/state/scanner_farm_loop.sqlite`
  (processed watches, queued jobs, prepared data, skips, per-cycle counters).
- **Farm queue:** `<private_root>/state/strategy_lab.sqlite` (existing queue table).
- **Prepared candles:** `<private_root>/market_data/<timeframe>/` (HDD/cold ok).
- **Compiled sweep specs:** `<private_root>/plans/event_specs/`.
- `<private_root>` = `TRADING_BOT_RESEARCH_ROOT`
  (default `~/github_projects/trading-bot-research/strategy-lab`), kept OUTSIDE this
  public repo.

## Limits (keep the desktop quiet)

- `--max-jobs-per-cycle` and `--max-data-prepares-per-cycle` bound work per cycle.
- The sweep itself is capped by the timeframe profile + resource policy
  (`configs/strategy_lab/{timeframe_profiles,resource_policy}.yaml`).
- Candle windows are capped at 2000 bars per fetch; 1m is trigger-only and is never
  swept.
- Hot/cold storage hygiene (log rotation, LRU cache) is handled by
  `src/research_lab/storage_policy.py`.

## Reading status

```bash
python -m scripts.strategy_lab.farm_queue_status
python -m scripts.strategy_lab.farm_queue_status --include-expired --refill-universe core_market
```

Shows: scanner rows seen, resolved/eligible, prepared-data count, queued jobs, skips
with **top skip reasons**, the farm queue (queued/running/completed/failed), and the
**age of the last successful cycle**.

### Structured skip reasons

`no_okx_instrument`, `too_short`, `fresh_listing_pending`, `missing_prepared_data`,
`provider_error`, `data_prepare_failed`, `readiness_not_assessed` (legacy/unassessed
watch), `already_queued`, `not_farm_eligible`, `prepare_cap_reached`.

## Stopping safely

- `Ctrl+C` — the loop catches it and exits cleanly after the current cycle.
- `--stop-file <path>` — create that file; the loop exits before the next cycle.
- Killing the process is safe too: the checkpoint means the next start resumes
  without reprocessing or duplicating queued jobs.

## What is NOT touched (hard safety boundary)

Paper / research only. Public OKX market-data and news endpoints only. The loop
never touches `.env`, never changes `AUTO_TRADE`, never enables live trading, never
calls order execution / private OKX endpoints, and never touches Telegram
credentials. Every queued artifact is research-only; watch-queue rows keep
`execution_allowed=false`.
