# Session handoff - 2026-07-03

Status: current handoff after the long farm/main-paper/Telegram recovery work.

## What is running now

The active process is the canonical Strategy Lab paper/research loop:

```text
python -X utf8 -u -m scripts.strategy_lab.farm_loop
  --loop --apply
  --run-worker --run-validation --run-paper --run-paper-signals
```

Last verified runtime:

- process: `python pid=18900`
- branch/head: `feature/calc-farm`, `7bbc65c feat: harden farm loop observability`
- stage: `sleep`
- loop: `true`
- paper mode: `paper_only=true`
- execution: `execution_allowed=false`
- `AUTO_TRADE=false`
- operational health: no blocking gates

This means the project is working as a **safe paper/research collector**. It is not live
trading and it is not the old live `main.py` executor.

## What has been built

The current chain is:

```text
scanner/intake + OKX discovery
  -> farm_loop / farm_coordinator
  -> strategy sweeps
  -> validator / PFR catalog
  -> paper_signals
  -> main_paper_bridge
  -> main_paper_consumer
  -> main_adaptive_policy
  -> main_paper_runtime_queue
  -> main_paper_runtime_observation
  -> main_paper_trade_ledger
  -> paper_telegram_preview / paper_telegram_delivery audit
  -> paper_signal_training_export
  -> scripts/journal.xlsx
```

Important recent proofs:

- full test suite: `1904 passed`, one existing CuPy/CUDA warning
- focused runtime/gate tests passed before push
- `operational_health` reports `ready_for_visible_paper_research_loop=pass`
- `main_paper_trade_ledger_available=pass`
- `paper_runtime_observed=pass`
- `paper_signal_training_export=pass`
- `.env` and `config.yaml` were not changed
- no `AUTO_TRADE=true`
- no live/private order execution path was enabled

## Current live counters from the latest check

Latest observed cycle around `farm cycle @ 1783046220`:

- `paper_ready: checked=500 ready=14 plan_ready=14 local_data_ready=14`
- `pfr_lane: pfr_records_loaded=53 pfr_passed_quality=43 pfr_unique_setups=11`
- `main_paper_bridge: instructions=1`
- `main_paper_consumer: accepted=1 rejected=0`
- `main_paper_runtime_queue: queued=1 action=watch_paper`
- `main_paper_runtime_observation: observed=1 provider_error=0`
- `main_paper_trade_ledger: trades=1 by_status={'armed': 1}`
- `paper_telegram_preview: rendered=1`
- `paper_telegram_delivery: dry_run=True sends_network=False` in that check
- `paper_signal_training_export: rows=1264`
- `calculator_advisor: processed=1 accepted=1`

The broad research paper-signal store is much larger:

- `paper_signals` rows: about `10070`
- statuses include `armed`, `opened_paper`, and `reviewed`
- those rows are research/training material unless a validator/PFR-backed
  `ready_strategy_id` lets them through the main-paper bridge.

## Critical expectation mismatch

The user expected something closer to:

```text
old/main-style paper executor:
  "what if we opened this?"
  -> produce a main trading forecast/card
  -> write the pseudo-trade
  -> later write the outcome
```

What currently exists is stricter:

```text
validator-backed paper watcher:
  only validated/PFR-backed active paper rows
  -> main-readable instruction
  -> public-candle watch_paper observation
  -> paper ledger/training
```

So, yes, the project runs. But the current main-paper path is not yet the old main engine
making broad independent forecasts. The old `main.py` remains isolated because it is
order-capable and imports private/execution-adjacent paths.

This is intentional safety, but it is not the same product behavior the user wants.

## Main design decision for the next chat

Do **not** wire farm outputs directly into old `main.py`.

The next correct step is to build a reviewed `main_paper_executor` contract that does the
"what if" behavior safely:

1. consumes validator/PFR-ready setups and optionally manual scanner requests;
2. computes deterministic entry/SL/TP/risk through shared math;
3. asks LLM only for bounded explanation/advice, not raw numbers or execution permission;
4. writes a pseudo-position/trade lifecycle;
5. produces human Telegram cards;
6. records outcome rows for training;
7. keeps `execution_allowed=false` until a separate live-execution review exists.

This executor should be separate from old `main.py`.

## LLM roles as currently understood

Current / safe:

- `calculator` via local Ollama: bounded advisory JSON over feature packets.
- It can suggest/reason inside schema.
- It cannot change entry, stop, take, validator verdict, paper-ready status, leverage, or execution.

Not complete yet:

- no persistent role-based trading swarm is operating as autonomous decision maker;
- no LLM is allowed to choose money execution;
- VIP/manual product surfaces are separate and need a product review before being treated
  as part of the canonical paper loop;
- Alibaba/Yandex product/VIP routes exist, but provider comparison and prompt quality work
  are not the core farm runtime.

## Telegram state

There are three concepts:

1. scanner/news/product Telegram surfaces;
2. Strategy Lab paper Telegram preview/delivery audit;
3. old manual/VIP Telegram analyzer.

The current farm loop produces paper Telegram previews and a delivery audit. Depending on
the launcher/env, the delivery can be dry-run or explicit-send. It must not be confused
with old main trading cards.

The user wants subscriber-facing cards that read like human main trading cards, not raw
technical JSON. That should be handled by the new paper executor/card layer, not by
weakening the validator gate.

## What to tell the next agent

Start by reading:

- `SESSION.md`
- this file
- `CURRENT_STATE.md`
- `ARCHITECTURE.md`
- `ROADMAP.md`
- `docs/farm_runbook.md`
- `docs/main_adaptive_policy_2026-06-30.md`

Then verify:

```powershell
python scripts/project_snapshot.py
python -m scripts.strategy_lab.operational_health --private-root C:\Users\krivo\github_projects\trading-bot-research\strategy-lab --pfr-db-path C:\Users\krivo\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite --json
git status --short
```

Do not assume that "process is running" means the user's desired main-paper product is
complete. The running loop is the safe research/paper backbone.
