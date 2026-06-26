# Farm Runbook - Active Operator Path

Status: **ACTIVE**. Last updated: 2026-06-26.

The calculation farm is now driven by `farm_loop`. The system is paper/research only:
public OKX market data, no `AUTO_TRADE`, no orders, no private account endpoints.
Telegram is a guarded surface only; it is not part of the farm decision path.

## Operator Preflight

Run this before a long paper/farm cycle. It is read-only, loads local environment
configuration, does not print secrets, and does not call exchange or Telegram providers.

```bash
python -m scripts.strategy_lab.operational_health \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" \
  --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

Expected safe state:

- `mode = paper_research_only`
- `auto_trade = false`
- scanner LLM provider/key presence visible, without secret values
- Telegram channel presence visible, without token/chat values
- journal, paper-signal, and PFR artifact paths resolved
- `readiness` gates show what is runnable, optional, or intentionally planned:
  PFR source, paper-signal store, main-readable instruction view, paper-only main
  consumer audit, offline paper Telegram preview, Telegram surfaces, LLM policy,
  journals, and the explicit `main_runtime_consumer = planned` boundary.

Treat a `planned` main-runtime consumer as a safety boundary, not as a launch failure.
The visible farm loop can produce and consume paper instructions into an audit view today;
the old main process must not be treated as their executor until a separate runtime adapter
is designed and tested.

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

# One bounded paper-signal/PFR smoke. It is intentionally capped for operator checks.
python -m scripts.strategy_lab.paper_signals_run --mode live --max-signals 1 --max-observe 0 --max-pfr-scan 2 --public-fetch-timeout 3

# One bounded full-cycle smoke with paper-signal/PFR lane enabled.
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --paper-signals-max-observe 0 --paper-signals-max-pfr-scan 1 --paper-signals-fetch-timeout 3 --max-plan-events 1 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-paper-cards 1 --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"

# Rebuild the main-readable paper instruction view from active paper signals.
python -m scripts.strategy_lab.main_paper_bridge --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"

# Validate that instruction view into a paper-only main consumer audit artifact.
python -m scripts.strategy_lab.main_paper_consumer --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"

# Build offline Telegram-card previews from accepted paper instructions. This does
# not call Telegram and does not read chat IDs or tokens.
python -m scripts.strategy_lab.paper_telegram_preview --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"

# Fast wiring smoke for the farm -> paper-watch -> main instruction -> Telegram preview chain.
# This intentionally disables worker/validation/paper execution and caps forward/paper generation at 0;
# the stage warning is expected. Use this to verify surfaces quickly before a long loop.
python -m scripts.strategy_lab.farm_loop --once --apply --provider synthetic --no-discovery-refresh --max-plan-events 0 --max-prepares 0 --max-enrich 0 --max-sweeps 0 --max-worker-jobs 0 --max-paper-cards 0 --max-followups 0 --no-followups --true-forward-max-candidates 0 --run-paper-signals --paper-signals-max-new 0 --paper-signals-max-pfr-scan 0 --paper-signals-max-observe 0 --paper-signals-fetch-timeout 1 --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"

# Continuous full cycle.
python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --sleep-seconds 180 --stop-file STOP --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --quiet

# Visible operator wrapper for the same continuous full cycle. The wrapper passes
# STRATEGY_LAB_PFR_DB_PATH by default, so the PFR bridge is active unless you
# override that environment variable.
bat\strategy_lab_farm_full_cycle_loop.bat

# Clean stop for the wrapper above.
bat\strategy_lab_farm_full_cycle_stop.bat

# Status, no raw log tailing needed.
python -m scripts.strategy_lab.status
python -m scripts.strategy_lab.farm_status_report
python -m scripts.strategy_lab.farm_status_report --json
```

`farm_loop` runs bounded feedback follow-ups by default. Use `--max-followups N` to cap
the number handled per cycle, or `--no-followups` only for diagnostics. A follow-up does
not bypass the worker: it becomes a typed lifecycle task first, then a normal sweep job.

## Paper Gate

`--run-paper` is gated by hard validation. Paper simulation reads only setup cards with:

- `lite_status == FORWARD_PAPER`
- `hard_status == PAPER_FORWARD_READY`
- `paper_forward_ready == true`
- executable params: `hold_bars`, `stop_pct`, `take_pct`
- percent-point units (`8` means 8%, not 0.08%)
- reward/risk at least 1:2 (`take_pct >= 2 * stop_pct`)

If validation produces no `PAPER_FORWARD_READY` cards, the paper step writes nothing and
prints readiness blockers. Current blockers such as `FAILED_COSTS` and `NEEDS_MORE_DATA`
mean the pipeline worked and refused to fake a paper setup. Do not manually promote these
statuses.

Positive and negative paper outcomes are both retained. Negative paper results are not
deleted or treated as "nothing"; status/reporting groups them as research evidence for
follow-up analysis.

## Paper Signals And PFR Bridge

`--run-paper-signals` enables the isolated paper-signal observation lane inside
`farm_loop`. The lane is still research/paper only:

- generated signals are written as JSONL/snapshots and visual review artifacts;
- `PFR` records are loaded only when `--pfr-db-path` is provided;
- PFR scanning is bounded by `--paper-signals-max-pfr-scan`;
- active signal observation can be capped with `--paper-signals-max-observe` for smoke
  checks;
- public data fetch timeout is controlled by `--paper-signals-fetch-timeout`;
- no signal can enable live order execution.
- after each `farm_loop --run-paper-signals` cycle,
  `src.research_lab.main_paper_bridge` rebuilds a main-readable paper instruction
  view with `paper_only=true` and `execution_allowed=false`.
- after the bridge export, `src.research_lab.main_paper_consumer` validates the shared
  `SignalContract` payload and writes a paper-watch audit view; rejected instructions
  are visible as contract rejects, not silently forwarded.
- after the consumer audit, `src.research_lab.paper_telegram_preview` builds offline
  Telegram-card previews and validates message length, HTML escaping, and execution
  disclaimers without sending anything.

For the standalone CLI, the matching flags are:

```bash
python -m scripts.strategy_lab.paper_signals_run \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" \
  --mode live \
  --max-signals 1 \
  --max-observe 0 \
  --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" \
  --max-pfr-scan 2 \
  --public-fetch-timeout 3
```

`max-observe=0` is for fast preflight only. Long paper-forward runs should observe active
signals normally.

## Journal And Training Data

The Excel journal is rebuilt locally:

```bash
python scripts/build_journal.py
```

Paper-signal outcomes can also be exported into a compact training-friendly JSONL
without touching private fills:

```bash
python -m scripts.strategy_lab.paper_signal_training_export \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

By default, rebuilds do not call private OKX account/fill endpoints. Manual private fills
are opt-in only:

```bash
set JOURNAL_ENABLE_PRIVATE_FILLS=1
python scripts/build_journal.py
```

Use that opt-in only when explicitly auditing account history. The farm/paper research loop
does not require it.

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
- `state/derived/paper_signal_training.jsonl` - derived training-friendly rows from
  terminal paper-watch outcomes and deterministic reviews.
- `state/derived/main_paper_instructions.jsonl` and
  `state/derived/main_paper_instructions.json` - rebuildable main-readable paper
  instruction view derived from active paper-watch signals; every row is
  `paper_only=true` and `execution_allowed=false`.
- `state/derived/main_paper_consumed.jsonl` and
  `state/derived/main_paper_consumed.json` - paper-only consumer audit over the
  instruction view; every accepted row is contract-validated and still has no execution
  authority.
- `state/derived/paper_telegram_preview.jsonl` and
  `state/derived/paper_telegram_preview.json` - offline Telegram-card previews for
  accepted paper instructions; `sends_network=false`, no token/chat values, no API call.
- `state/derived/setup_lifecycle.json` - optional rebuildable snapshot of setup lifecycle
  groups; canonical data remains in the DBs and artifacts above.
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
