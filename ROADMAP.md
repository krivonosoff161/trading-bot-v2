# ROADMAP - Current Project Direction

Updated: 2026-06-10

This is the current roadmap for `trading-bot-v2`. Older roadmap and service-pivot
documents are preserved as history, but they no longer define the active work.

## Current Thesis

The active project is an **info-edge scanner** for market events, not an
auto-trading bot.

The scanner should:

- collect broad raw information;
- route events to the right asset and layer;
- distinguish expected / realized / context events;
- keep a full paper journal;
- measure outcomes against baselines;
- learn which sources, filters and event families are useful;
- surface only high-value `GO` / `WATCH` cards to Telegram.

The current scanner is useful as a data and decision journal, but it is not yet a
calibrated trading engine.

## Current State

Done:

- `src/scout/` created as the active scanner home.
- Five layers configured: L1 crypto majors, L2 alts/memes, L3 metals, L4 energy,
  L5 equities/proxies.
- SQLite intake buffer is the default scanner runtime.
- Layer agents, orchestrator, chief model and LLM role routing exist.
- Scanner journal, ingest log, drops log, routing audit, reasoning/events records,
  outcomes and training/memory records exist.
- L2/L3/L4/L5 source coverage was expanded.
- Strong cross-layer asset fallback exists for cases such as Coinbase, SpaceX and
  Anthropic mentioned from sources outside their normal layer.
- Stage 0 recall fixes were applied and the scanner has accumulated live paper
  data.
- First v0.6 hygiene pass is implemented:
  - Telegram sends `GO` / `WATCH` chief cards by default;
  - `NO_GO` stays in logs/training data unless `SCANNER_SEND_NO_GO=true`;
  - cards are layer-aware and verdict-specific;
  - `resolve_outcomes.py --limit N` prevents long silent backlogs;
  - `calibration_report.py` measures missed `NO_GO` by useful dimensions.

Known current problems:

- The scanner is over-conservative: too many cards end as `NO_GO`.
- Some `NO_GO` cards are followed by real movement, so filters and prompts are not
  calibrated yet.
- New Telegram/card behavior needs live observation to ensure `WATCH` does not
  become spam.
- Calibration reports must be compared after fresh outcomes accumulate.
- Macro/context headlines without one clean asset need a separate context path.

## Near-Term Roadmap

### v0.6 - Calibration And Telegram Hygiene

Goal: make the current scanner easier to judge and less noisy.

Tasks:

- done: gate Telegram output to `GO` / `WATCH` by default;
- done: keep all `NO_GO` cards in logs and training data;
- done: first layer-specific, verdict-specific card wording pass;
- done: bounded outcome resolver with `--limit`;
- done: add a calibration report:
  - missed `NO_GO` by source;
  - missed `NO_GO` by layer;
  - missed `NO_GO` by asset;
  - missed `NO_GO` by phase;
  - missed `NO_GO` by lead class;
  - missed `NO_GO` by chief-called / low-confidence;
- next: run the new behavior for several sessions and compare fresh reports;
- next: tune source/layer thresholds from evidence.

Exit criteria:

- Telegram is quiet by default.
- Source/layer calibration report is reproducible locally.
- The project can explain why `NO_GO` dominates without treating that dominance as
  success.
- `WATCH` stays selective rather than becoming a second noisy channel.

### v0.7 - MARKET_CONTEXT / WATCH_MARKET

Goal: stop forcing macro or cross-market context into single-asset trade verdicts.

Tasks:

- define `MARKET_CONTEXT` / `WATCH_MARKET` schema;
- capture macro, regulation, geopolitics, stablecoin, tax and policy headlines;
- attach affected assets without emitting trade recommendations;
- write context records for later analysis;
- decide how context interacts with scanner cards.

Exit criteria:

- no-single-asset headlines have a clean destination;
- context can be measured later without polluting trade candidates.

### v0.8 - Source Quality And Intake Discipline

Goal: identify which sources deserve tokens and attention.

Tasks:

- refine source-quality dashboard/reporting;
- compare RSS/aggregator/official/native feeds;
- identify sources that are mostly late recaps;
- identify sources that produce real watch candidates;
- tune layer-specific materiality thresholds conservatively.

Exit criteria:

- clear source ranking by layer;
- explicit keep / reduce / park decisions for each active source family.

### v0.9 - Surprise And Pending Events

Goal: turn "expected vs realized" into measurable state instead of prompt guessing.

Tasks:

- expand `pending_events.jsonl` use beyond skeleton records;
- match realized events against expected/pending records;
- add basic surprise classes: timing, magnitude, direction, mechanics, none;
- keep surprise computation deterministic where possible.

Exit criteria:

- at least one layer has real expected->realized lifecycle measurement.

### v1.0 - Stable Research Scanner

Goal: a stable paper scanner that can run continuously and produce trustworthy
diagnostics.

Tasks:

- stable commands and docs;
- clean operational handoff;
- reproducible local quality reports;
- clear boundaries between active scanner, frozen engines and archives;
- no stale top-level documentation.

## Future Tracks

These are intentionally not current work:

- live auto-trading;
- real-money execution;
- a new `main_event_engine`;
- full GUI/SaaS wrapper;
- paid data integrations;
- Telegram account listener / Telethon production feed;
- broad multi-agent market-debate platform.

They may become relevant only after the scanner proves source quality and forward
measurement discipline.
