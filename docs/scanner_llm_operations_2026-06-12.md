# Scanner and LLM Operations Plan

Status: 2026-06-12
Scope: paper-only scanner, main technical-analysis process, and private strategy-lab research.

This document describes how the project should run paid LLM work without losing
cost control, data discipline, or the separation between public code and private
research results.

## Operating Principle

Python owns data, gates, accounting, logs, and safety boundaries.

LLMs are used as analysts inside bounded roles:

- cheap model: extracts structured facts, uncertainty, and candidate context;
- code gate: decides whether a candidate is worth chief review;
- chief model: reviews only selected candidates and writes a trading-readable
  explanation;
- audit model: later reviews batches, reports, and candidate libraries, not live
  orders.

No LLM is allowed to place orders, bypass budget caps, or turn a weak event into
a trade just because the text sounds convincing.

## Current Live Roles

News scanner:

- reads event/news sources into `data/scout/news_buffer.sqlite`;
- extracts machine-readable docs once;
- runs cheap layer agents on normalized events;
- escalates selected candidates to chief;
- sends only `GO` / `WATCH` chief cards to Telegram by default;
- keeps `NO_GO`, drops, routing, reasoning, usage, and outcomes in logs.

Main process:

- remains the technical-analysis and market-state side;
- should consume scanner watch rows only through an explicit confirmation bridge;
- should not automatically trade scanner output.

Private strategy lab:

- lives outside the public repository, by default under
  `%USERPROFILE%\github_projects\trading-bot-research`;
- stores real findings, parameters, candidate scorecards, graph exports, and
  Obsidian notes;
- public repos may show methods and sanitized examples, not profitable private
  result tables.

## LLM Budget Guard

The LLM client has an opt-in local cost guard. Recommended starting settings for
the real `.env` once paid Alibaba usage is enabled:

```env
LLM_STOP_ON_BUDGET=true
LLM_DAILY_RUB_CAP=100
LLM_SCAN_RUB_CAP=10
LLM_MAX_TOKENS_PER_SCAN=80000
LLM_MAX_CHIEF_PER_SCAN=5
```

Meaning:

- daily cap: hard stop for the current UTC day based on scanner cost logs;
- scan cap: hard stop within one scanner pass;
- token cap: prevents a broken article or prompt from exploding context;
- chief cap: keeps the expensive model rare even when sources become noisy.

When a cap is hit, the client returns `status=budget_skipped` without making a
network call. Chief skips are written as `CHIEF_BUDGET_SKIPPED` and are not sent
to Telegram.

Health check:

```bash
python src/scout/llm_health_report.py --day 2026-06-11
```

Live probe is explicit because it spends tokens:

```bash
python src/scout/llm_health_report.py --probe-live
```

## Asset And Layer Plan

L1 - majors:

- BTC, ETH, SOL, XRP and broad market context;
- needs ETF flows, funding/open interest, dominance, and market regime context;
- scanner output is often market-context, not a direct trade.

L2 - alts, memes, high-volatility crypto:

- strongest current source of idiosyncratic misses and opportunities;
- needs DexScreener quality metrics, unlocks, listings, token risk, social/flow
  context, and strict scam/veto flags;
- high priority for strategy-lab simulations because volatility is large.

L3 - metals:

- XAU, XAG, XPT, XPD;
- needs event surprise and macro context more than generic headlines;
- current evidence says many metal headlines are correctly filtered as `NO_GO`.

L4 - energy:

- CL, NG and energy equities where available;
- needs EIA actual vs consensus, OPEC schedule, geopolitics, and per-asset
  synthesis so several contradictory oil cards do not spam the operator.

L5 - equities, public companies, pre-IPO/perp themes:

- SEC filings, company IR, press releases, earnings and listing mechanics;
- needs primary-document extraction first, then cheap-model extraction;
- scanner should distinguish official primary signal from media recap.

## Research Sequence

1. Inventory and data quality

   - refresh manifests in the private `strategy-lab`;
   - identify symbols/timeframes with enough clean data;
   - mark missing data and unusable regimes before testing strategies.

2. Strategy-family sweep

   - start with known public families: breakout, mean reversion, trend,
     volatility squeeze, funding/futures pressure, liquidity/volume shock,
     news-event continuation, and fade setups;
   - run many cheap simulations, but store detailed results only in the private
     repo.

3. Filter and regime discovery

   - vary filters by asset class, volatility regime, session, trend state,
     liquidity, spread, news/event context, and holding period;
   - LLM may propose new combinations, but code must execute and score them.

4. Validation pressure

   - candidates go through honest-backtest style checks: out-of-sample,
     walk-forward, parameter stability, costs, slippage, drawdown, and forward
     paper replay;
   - a candidate is not "good" until it survives these gates.

5. Private candidate registry

   - accepted and rejected candidates are recorded in private registry files;
   - include why it passed/failed, required regime, symbols, timeframes,
     fragile assumptions, and next review date.

6. Graph and notes

   - export relationships between strategy family, filter, regime, asset
     cluster, and result state into `strategy-lab/graph`;
   - write human notes in `strategy-lab/obsidian-vault`.

## What Must Stay Private

Keep these out of public repos:

- exact profitable parameter sets;
- private candidate rankings;
- real symbol/regime/filter combinations that passed validation;
- trade-level simulation outputs that reveal a working edge;
- cost-sensitive source-quality conclusions not yet published deliberately.

Public repos may contain:

- code;
- schemas;
- safe examples;
- methodology;
- generic lessons;
- redacted reports.

## Immediate Operating Checklist

Before leaving the scanner running on paid models:

1. Confirm provider and daily spend:

   ```bash
   python src/scout/llm_health_report.py --day 2026-06-11
   ```

2. Enable local caps in `.env`.

3. Smoke the scanner without cards:

   ```bash
   python -u src\scout\scanner_v0.py --buffer --limit 0
   ```

4. Run the scanner normally:

   ```bash
   scanner.bat
   ```

5. After 24 hours:

   ```bash
   python src/scout/chief_usage_report.py
   python scripts/analysis/source_onboarding_report.py
   python -X utf8 src/scout/calibration_report.py
   ```

6. Move conclusions and candidate details into the private research repo, not
   into public docs.

## Strategy-Lab Runner

Operator start:

```bash
bat\strategy_lab_start.bat
```

This builds the data inventory, syncs the state DB, ensures one smoke
experiment is queued if the same spec is not already queued/running, starts the
dashboard, starts the one-worker queue loop, and opens `http://127.0.0.1:8765`.

Smoke demo:

```bash
bat\strategy_lab_demo_all.bat
```

This syncs the DB, queues `configs/strategy_lab/l2_smoke.json`, runs one queued
job, starts the dashboard, and opens the browser.

First working command:

```bash
python scripts/strategy_lab/run_experiment.py --spec configs/strategy_lab/l2_smoke.json
```

Continuous loop:

```bash
bat\strategy_lab_loop.bat
```

Read-only local dashboard:

```bash
bat\strategy_lab_dashboard.bat
```

Open:

```text
http://127.0.0.1:8765
```

The first dashboard intentionally has no write actions. It reads completed
private runs, scanner LLM cost logs, and Obsidian paths. It does not read or
display `.env`, does not execute shell commands from the UI, and does not import
the live trading path.

SQLite state DB:

```bash
bat\strategy_lab_sync_db.bat
```

This imports completed private runs into
`strategy-lab/state/strategy_lab.sqlite`. The DB is an index and queue state
store; raw metrics, candidate tables, and Obsidian notes remain in the private
workspace.

Queue one smoke experiment:

```bash
bat\strategy_lab_enqueue_smoke.bat
```

Run one queued job:

```bash
bat\strategy_lab_worker_once.bat
```

Run the local worker loop:

```bash
bat\strategy_lab_worker_loop.bat
```

The worker handles one queued job per pass. This keeps 24/7 research bounded:
the loop can sleep between jobs, the queue is visible in the dashboard, and each
job writes files first before the DB is updated.

Data inventory (run before queueing new specs):

```bash
python scripts/strategy_lab/build_data_inventory.py --spec configs/strategy_lab/l2_smoke.json
```

This scans the spec's `data_glob`, classifies every file as
usable / too_short / malformed and flags spec symbols with no usable data as
missing. The report goes to the private `strategy-lab/inventory/` folder; the
console prints only counts.

Current implementation (MVP 2.0):

- reads a JSON experiment spec (optionally with `filters` for regimes);
- loads local OKX-history JSON candles;
- evaluates strategies from the public strategy registry
  (`src/research_lab/strategy_registry.py`, 12 deterministic strategies across
  breakout / mean_reversion / trend / volume families);
- labels each trade entry with a regime (volatility / trend / volume buckets,
  computed without lookahead) and aggregates a per-regime breakdown;
- grades each run (PROMOTE_FOR_PRESSURE_TEST / OBSERVE / REJECT), then
  validator-lite assigns a validation status:
  `REJECT` / `OBSERVE` / `REGIME_SPECIFIC` / `FORWARD_PAPER`;
- writes private outputs to `trading-bot-research/strategy-lab`;
- upserts every graded candidate into the private candidate registry
  (`strategy-lab/candidate-registry/candidates.jsonl`);
- indexes completed runs and queue jobs in `state/strategy_lab.sqlite`;
- emits `metrics.json`, `candidates.csv`, `summary.md`, `graph_edges.csv`,
  `llm_review_pack.json`, `llm_review_prompt.md`, and Obsidian notes under
  `obsidian-vault/`.

Validation statuses are research labels, not profitability claims:
`PROMOTE_FOR_PRESSURE_TEST` only means "worth validating", and
`FORWARD_PAPER` only means "passed lite gates; track paper-forward".

The runner stays code-first and LLM-later. LLM review consumes only the
aggregate `llm_review_pack.json` / `llm_review_prompt.md` after code metrics
exist; the pack asks the LLM to propose next experiment specs as drafts, and
nothing the LLM produces is auto-enqueued.
