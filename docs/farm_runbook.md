# Farm Runbook - Active Operator Path

Status: **ACTIVE**. Last updated: 2026-06-19.

The calculation farm is now driven by `farm_loop`. The system is paper/research only:
public OKX market data, no `.env`, no `AUTO_TRADE`, no orders, no private account
endpoints, no Telegram.

## Active Path

- **Core:** `python -m scripts.strategy_lab.farm_loop`
  (brain DB: `state/farm_tasks.sqlite`).
- **Visible one-click wrapper:** `bat\strategy_lab_farm_full_cycle_loop.bat`.
- **Clean stop wrapper:** `bat\strategy_lab_farm_full_cycle_stop.bat`.
- **Compute executor:** `worker_once` / `worker_loop` drain `state/strategy_lab.sqlite`.
  In the normal loop, `--run-worker` drains a bounded number of jobs per cycle.
- **Operator status:** `python -m scripts.strategy_lab.status` and
  `python -m scripts.strategy_lab.farm_status_report`.
- **Legacy/off-default:** `scanner_farm_loop`, `universe_farm_loop`, `research_loop`,
  `strategy_lab_start.bat`. Keep them for diagnostics/history; do not build new operator
  work on top of them.

## Prerequisite: OKX Universe Snapshot

Build or refresh the keyless OKX instrument snapshot:

```bash
python -m scripts.strategy_lab.discover_okx_universe --apply
```

Without the snapshot, `farm_loop` can still consume scanner/watch intake and existing
prepared data, but broad `discovery_refill` has nothing to pull from. The loop will report
`blocked:no_eligible_tasks` instead of inventing work.

## Commands

```bash
# Plan only, writes nothing.
python -m scripts.strategy_lab.farm_loop --once --dry-run

# One real cycle: prepare/enrich/queue/compute/classify.
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding --enrich-oi

# One full cycle: compute -> honest validation -> paper readiness/paper outcomes.
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --enrich-funding --enrich-oi

# Continuous full cycle.
python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --run-paper --enrich-funding --enrich-oi --sleep-seconds 180 --stop-file STOP --quiet

# Visible operator wrapper for the same continuous full cycle.
bat\strategy_lab_farm_full_cycle_loop.bat

# Clean stop for the wrapper above.
bat\strategy_lab_farm_full_cycle_stop.bat

# Status, no raw log tailing needed.
python -m scripts.strategy_lab.status
python -m scripts.strategy_lab.farm_status_report
python -m scripts.strategy_lab.farm_status_report --json
```

## Paper Gate

`--run-paper` is gated by hard validation. Paper simulation reads only setup cards with:

- `lite_status == FORWARD_PAPER`
- `hard_status == PAPER_FORWARD_READY`
- `paper_forward_ready == true`
- executable params: `hold_bars`, `stop_pct`, `take_pct`

If validation produces no `PAPER_FORWARD_READY` cards, the paper step writes nothing and
prints readiness blockers. Current blockers such as `FAILED_COSTS` and `NEEDS_MORE_DATA`
mean the pipeline worked and refused to fake a paper setup. Do not manually promote these
statuses.

## Data And Artifacts

`<private_root>` is `TRADING_BOT_RESEARCH_ROOT`, defaulting to:

```text
%USERPROFILE%\github_projects\trading-bot-research\strategy-lab
```

Apply mode refuses to write inside the public repo unless `--allow-public-output` is
explicitly passed.

- `market_data/<tf>/*.json` - prepared candles with optional funding/OI fields.
- `state/farm_tasks.sqlite` - lifecycle brain: task type/state/reason/fingerprint.
- `state/strategy_lab.sqlite` - compute queue, runs, candidates, farm/paper results.
- `plans/event_specs/*.json` - materialized sweep specs, bounded by storage policy.
- `hard_validation/{requests,verdicts,reports}/` - honest validation artifacts.
- `setup_library/{cards,reports,setup_index.jsonl}` - validated setup cards.
- `paper/paper_trades.jsonl` - paper trade journal.
- `logs/farm/{cycle_log,task_transitions,errors}.jsonl` - structured farm logs.
- `logs/farm_full_cycle_loop.log` - console log from the visible wrapper.

## Stop / Restart

The loop is restart-safe. State persists in `farm_tasks.sqlite` and `strategy_lab.sqlite`;
deduplication uses task keys, fingerprints, and TTLs.

- Stop the canonical wrapper: `bat\strategy_lab_farm_full_cycle_stop.bat`.
- Stop a raw CLI loop: create the file passed via `--stop-file`, or press Ctrl+C.
- Restart: run the same command again.
- Worker recovery: `worker_once` reaps stale jobs; manual fallback is
  `python -m scripts.strategy_lab.requeue_stale_jobs`.

## Storage Hygiene

Every apply cycle runs storage maintenance:

- farm logs are rotated;
- event specs are capped;
- terminal lifecycle rows and unique candidates are bounded;
- market data stays under the configured private root, so point
  `TRADING_BOT_RESEARCH_ROOT` at the HDD if the SSD must stay clear.

## Safety Boundary

The farm and paper runtime do not import the money path. Boundary tests cover the new
modules. Forbidden without explicit approval: `.env`, `AUTO_TRADE`, order execution,
private exchange/account endpoints, Telegram credentials, old main engine as executor.
