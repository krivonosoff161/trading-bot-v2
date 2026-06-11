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

- lives in `C:\Users\krivo\github_projects\trading-bot-research`;
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

