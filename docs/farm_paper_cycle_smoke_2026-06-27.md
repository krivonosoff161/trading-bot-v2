# Farm/Paper Cycle Smoke Audit (2026-06-27)

Status: **visible paper/research cycle verified; no live trading enabled**.

This audit records the bounded wiring smoke after the farm/PFR/paper/main-paper
recovery work. It is intentionally not a strategy-performance report. It verifies that
the current launch path can rebuild the paper chain, observe the paper queue, render
Telegram previews, refresh training rows, and keep the old product/main execution
surfaces isolated.

## Scope

Verified path:

```text
farm_loop --run-paper-signals
  -> paper_signals / optional PFR seeding
  -> main_paper_bridge
  -> main_paper_consumer
  -> main_paper_runtime_adapter
  -> main_paper_runtime observer
  -> paper_telegram_preview
  -> paper_telegram_sender dry-run audit
  -> paper_signal_training_export
  -> scripts/build_journal.py
```

Out of scope:

- old live `main.py` as executor;
- `start.bat` / legacy Telegram analyzer as farm launcher;
- Telegram network send;
- `.env`, `AUTO_TRADE`, private OKX endpoints, order placement;
- performance or edge claims.

## Commands Run

Visible Windows wrapper dry-run, capped to avoid compute and network sends:

```bat
set STRATEGY_LAB_NO_PAUSE=1
set STRATEGY_LAB_FARM_ONCE=1
set STRATEGY_LAB_FARM_DRY_RUN=1
set STRATEGY_LAB_FARM_MAX_PREPARES=0
set STRATEGY_LAB_FARM_MAX_ENRICH=0
set STRATEGY_LAB_FARM_MAX_SWEEPS=0
set STRATEGY_LAB_FARM_MAX_WORKER_JOBS=0
set STRATEGY_LAB_FARM_MAX_VALIDATIONS=0
set STRATEGY_LAB_FARM_MAX_PAPER_CARDS=0
set STRATEGY_LAB_PAPER_SIGNALS_MAX_OBSERVE=0
set STRATEGY_LAB_PAPER_SIGNALS_MAX_PFR_SCAN=0
set STRATEGY_LAB_PAPER_SIGNALS_PFR_RESERVED=0
set STRATEGY_LAB_MAIN_PAPER_RUNTIME_LIMIT=0
bat\strategy_lab_farm_full_cycle_loop.bat
```

Fast wiring smoke, capped to avoid compute:

```bash
python -X utf8 -m scripts.strategy_lab.farm_loop \
  --once --apply \
  --run-worker --run-validation --run-paper \
  --provider synthetic --no-discovery-refresh \
  --max-plan-events 0 --max-prepares 0 --max-enrich 0 --max-sweeps 0 \
  --max-worker-jobs 0 --max-validations 0 --max-paper-cards 0 \
  --max-followups 0 --no-followups --true-forward-max-candidates 0 \
  --run-paper-signals --paper-signals-max-new 0 --paper-signals-pfr-reserved 0 \
  --paper-signals-max-pfr-scan 0 --paper-signals-max-observe 0 \
  --paper-signals-fetch-timeout 1 --main-paper-runtime-limit 0 \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Journal refresh:

```bash
python -X utf8 scripts/build_journal.py
```

Universe refresh:

```bash
python -X utf8 -m scripts.strategy_lab.discover_okx_universe --apply
```

Preflight:

```bash
python -X utf8 -m scripts.strategy_lab.operational_health \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" \
  --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" \
  --fail-on-blocked
```

## Observed Counts

Fast smoke:

- active tasks: 60;
- queue states: `blocked=3`, `completed=5000`, `deferred=12`, `queued=45`;
- blocked reason: `prepare_backoff:provider_error=3`;
- deferred reason: `too_short=12`;
- `paper_ready`: `checked=500`, `ready=14`, `plan_ready=14`, `local_data_ready=14`;
- `main_paper_bridge`: `instructions=10`, `paper_only=true`, `execution_allowed=false`;
- `main_paper_consumer`: `read=10`, `accepted=10`, `rejected=0`, `paper_only=true`;
- `main_paper_runtime_queue`: `read=10`, `queued=10`, `invalid=0`, `action=watch_paper`;
- `paper_telegram_preview`: `rendered=10`, `invalid=0`, `sends_network=false`;
- `paper_telegram_delivery`: `eligible=10`, `sent=0`, `errors=0`, `dry_run=true`;
- `paper_signal_training_export`: `rows=652`, `terminal_only=true`, `paper_only=true`.

Post-refresh preflight:

- `paper_chain_counts=pass`;
- `paper_runtime_observed=pass`;
- `paper_signal_training_export=pass`;
- `excel_journal.stale_vs_training=false`;
- `ready_for_visible_paper_research_loop=pass`;
- `product_analyzer_launch_contract=pass`;
- `scanner_llm_provider=pass`.
- `operator_next_actions.launch_blocked=false`;
- `operator_next_actions.blocking=[]`;
- `operator_next_actions.rebuild_actions=[]`.

Visible wrapper dry-run:

- wrapper preflight passed with `--fail-on-blocked`;
- wrapper printed `mode = --dry-run --once`;
- `pivot=discovery_refill`;
- `active_tasks=9`;
- `events_consumed=1`;
- `tasks_created=9` in dry-run output;
- process exit code: `0`.

Expected remaining operator items:

- `paper_telegram_surface=warn`: `PAPER_CHAT_ID` is not configured. This is an
  operator notification setting, not a compute blocker.
- `main_runtime_consumer=planned`: old live `main.py` remains intentionally isolated;
  paper lifecycle is handled by `main_paper_runtime`.
- `manual_product_analyzer_boundary=warn`: manual chart/latest analyzers are product
  surfaces, not farm/PFR paper runtimes.
- `telegram_analyzer_llm_provider_review=pass`: text-only analyzer calls are routed
  through the shared provider path by the reviewed launchers. Premium vision remains
  a separate future review.

## Discovery Snapshot

The keyless OKX universe refresh returned:

- total instruments: 386;
- `crypto_major=5`;
- `meme_or_high_beta=14`;
- `tokenized_equity=11`;
- `commodity=6`;
- `crypto_alt=350`;
- no new, delisted, or group-changed instruments in this refresh.

## Boundary Verdict

The restored visible paper/research cycle is assembled and observable. The old product
surfaces remain separated:

- `start.bat` is not the farm/PFR launcher;
- Telegram bot `main()` does not start the legacy `_scanner_loop`;
- `scripts.analyze_chart` does not send Telegram by default;
- `scripts.run_latest_analysis` gates old `auto_execute` behind
  `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE` and `AUTO_TRADE`;
- old `main.py` does not consume the paper queue;
- `execution_allowed=false` throughout the current paper path.

This audit does not claim a profitable strategy. It proves the current research/paper
chain can run without accidentally crossing into live execution or legacy product
delivery.
