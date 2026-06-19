# Farm Loop — Continuous Research Lifecycle (canonical)

Status: **ACTIVE** · Last updated: 2026-06-19 · Branch: `feature/calc-farm`

This is the authoritative description of the calculation farm's continuous research
cycle. It supersedes the older `universe_farm_loop` / `scanner_farm_loop` operator
docs (see [farm_ownership_map.md](farm_ownership_map.md)). Paper/research only — no
`.env`, no `AUTO_TRADE`, no order execution, no private exchange endpoints, no Telegram.

## What it is

`scripts/strategy_lab/farm_loop.py` drives `research_lab.farm_coordinator.run_coordinator_cycle`,
a self-deciding cycle that, every pass, picks the next meaningful step for every symbol
and records it as a typed lifecycle task with a machine-readable reason. It **never spins
on `already_queued`**: when fresh work is saturated it pivots to deferred-eligible work,
universe discovery, or reports `blocked:no_eligible_tasks`.

## Two databases (brain vs compute)

| DB | Module | Role |
|----|--------|------|
| `state/farm_tasks.sqlite` — **BRAIN** | `farm_tasks_db.py` | Typed-task lifecycle: decides *what / when*. |
| `state/strategy_lab.sqlite` — **COMPUTE QUEUE** | `state_db.py` | The proven sweep queue the worker drains. |

A `run_sweep` brain-task **materializes** into the compute queue via
`ensure_experiment_queued` and links back through `materialized_queue_job_id`. The brain
is the only thing that decides; the queue only executes. (The legacy `scanner_farm_loop`
wrote the compute queue directly with no brain — that flat path was the source of the
`already_queued` saturation this layer removes.)

## Task types & states

Task types: `intake_event` · `resolve_instrument` · `prepare_data` · `enrich_funding` ·
`enrich_oi` · `run_sweep` · `classify_result` · `export_validation` ·
`run_or_refresh_validation` · `schedule_followup`.

States: `queued` · `running` · `deferred` · `blocked` · `completed` · `skipped` · `failed`.
Every non-running state carries a `machine_reason` (e.g. `data_missing`, `too_short`,
`NEEDS_OI_DATA`, `NEEDS_FLOW_DATA`, `NEEDS_MICRO_DATA`, `prepare_backoff:*`, `data_ready`,
`gate_cleared`, `compute_completed`, `compute_deduped`).

### Re-arm (why it does not saturate)

`run_sweep` `task_key = run_sweep::SYMBOL::TF::FAMILY::<data_fingerprint>`. `enqueue_task`
returns *not-created* when an **active** task with that key exists, or when an identical
task **completed within the TTL** (`DEFAULT_TTL_SECONDS = 12h`). It re-arms (new task) only
when the **data fingerprint changes** (fresh candles / enrichment) or the TTL elapses. The
spec filename also embeds the fingerprint, so the compute layer recomputes on fresh data
instead of dedup-blocking forever. Data-gated families use a fingerprint-independent
`...::gate` key so they hold exactly one blocked slot until the gate clears.

## The cycle (one pass of `run_coordinator_cycle`)

1. **intake** — ingest intake events (scanner watches via `intake_adapter.watches_to_intake`,
   read from the watch *file*, never the scanner module; plus OKX discovery on the pivot).
   Dedup by `symbol+source+reason+time-window`.
2. **plan** — `data_planner.plan_symbol` decides per (symbol, eligible timeframe):
   missing → `prepare_data`; `too_short`/fresh-listing → **defer to a concrete time**;
   data ready → `run_sweep`; OI/funding family without its field → **block** with
   `NEEDS_OI_DATA` / `NEEDS_FLOW_DATA` and request an `enrich_oi` / `enrich_funding` task;
   microstructure → block `NEEDS_MICRO_DATA` (no public provider — see Limitations).
   Timeframes are asset-class-driven (`readiness_profiles.yaml`).
3. **unblock** — a blocked `run_sweep` flips back to `queued` when its gate clears
   (`oi`/`funding` field present on candles, or an OI slot file).
4. **execute** (apply only) — drain a bounded number of `prepare_data` (fetch candles;
   on success re-plan the symbol into `run_sweep` *same cycle*), `enrich_funding`,
   `enrich_oi`, then materialize `run_sweep` tasks into the compute queue.
5. **sync** — a finished compute job completes its `run_sweep` task and spawns
   `classify_result`.
6. **classify** — read the run's `metrics.json` → upsert `unique_candidates`
   (keyed `symbol::tf::family::params_hash::data_fingerprint`) → spawn `export_validation`
   for FORWARD_PAPER / REGIME_SPECIFIC. The export task is keyed by the full `uc_key`,
   not raw `candidate_id`, because one raw id can reappear under different timeframes
   or data fingerprints.
7. **validation** (apply + `--run-validation`) — `validation_orchestrator.run_due_validations`:
   export requests from `farm_tasks.sqlite.unique_candidates` (canonical; legacy
   `candidate-registry` is fallback for old tools only) → honest-backtest bridge
   (in-process) → **stamp verdicts back** into `farm_results` *and* `unique_candidates`
   → write `setup_library` cards. `PAPER_FORWARD_READY` cards are the only inputs the
   paper runtime can read. No manual file carry.
8. **paper** (optional, apply + `--run-paper`) — read only `PAPER_FORWARD_READY`
   setup cards, build `PaperTradePlan`, simulate against local prepared candles, and
   append `paper_outcomes`. If no card is ready, the cycle prints paper-readiness
   blockers instead of silently reporting `cards=0`.
9. **pivot** — `work_available` (more queued/deferred-ready) / `advanced_lifecycle` /
   `discovery_refill` (pull uncovered discovered symbols) / `blocked:no_eligible_tasks`.

## OI / funding / microstructure data

- **funding** — keyless public `funding-rate-history` (`OkxPublicFundingProvider`),
  forward-filled onto candles (`merge_funding`). Enabled with `--enrich-funding`.
- **open interest** — keyless public per-instId `open-interest-history`
  (`OkxPublicOpenInterestProvider`, paginates backward via the `end` cursor),
  forward-filled as the `oi` field (`merge_oi`). Enabled with `--enrich-oi`. A merged
  `oi` field clears `NEEDS_OI_DATA` automatically through the unblock step. A manually
  recorded `oi_slot` file remains a fallback.
- **microstructure** — **no keyless public provider** for `obi_top5`/`trade_delta_100`/
  `spread_bps`. These families stay `blocked: NEEDS_MICRO_DATA` (honest dead-end, visible
  in the dashboard `blocked_reasons`). See [BACKLOG.md] for the future task.

## Logs (see farm_journal.py)

Structured append-only JSONL under `<private_root>/logs/farm/`, rotated by
`storage_policy.maintain`:
- `cycle_log.jsonl` — one row per cycle (pivot, counters, by_state, blocked/deferred reasons);
- `task_transitions.jsonl` — one row per state change (the audit trail; via the
  `FarmTasksDB.on_transition` hook);
- `errors.jsonl` — worker / cycle errors needing human attention.

`--loop` prints a full block only on change/error, else a one-line heartbeat (`--verbose`
forces full, `--quiet` suppresses). Operators read structured state via
`farm_status_report` / the cockpit, not raw logs.

## Commands

```
# plan only, writes nothing (in-memory task DB):
python -m scripts.strategy_lab.farm_loop --once --dry-run

# one real cycle (prepare/enrich/queue/compute/classify):
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding --enrich-oi

# full loop incl. honest validation + paper simulation:
python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --run-paper \
    --enrich-funding --enrich-oi --sleep-seconds 180 --stop-file STOP

# operator picture:
python -m scripts.strategy_lab.farm_status_report
```

Stop a `--loop` run by creating the `--stop-file` or Ctrl+C. State persists in the two
sqlite DBs, so restart is safe and idempotent. See [farm_runbook.md](farm_runbook.md).

## Limitations / honest gaps

- Microstructure has no public provider → permanent `NEEDS_MICRO_DATA` block (by design).
- OI history is keyless but ~100 points/call (paginated); treat OI as optional context.
- Paper runtime is gated by hard validation. If `setup_library/cards` has no
  `paper_forward_ready=true` cards, `paper_loop` / `farm_loop --run-paper` reports the
  hard-status and plan/data blockers and does not simulate forward trades.
- `farm_loop` is not yet wired into any `.bat`; the legacy loops remain the live operator
  path until the switch is made deliberately (see [farm_ownership_map.md](farm_ownership_map.md)).
