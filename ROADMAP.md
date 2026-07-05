# ROADMAP - Current Project Direction

Updated: 2026-07-05

This is the current roadmap for `trading-bot-v2`. Older roadmap and service-pivot
documents are preserved as history, but they no longer define the active work.

## Current Thesis

> **Update 2026-07-05 - self-improvement track is now explicit.**
> The next work is not a new trading brain. It is a closed paper/research
> learning loop over existing artifacts: `TrainingRow.v2 -> OutcomeLearningCase.v1
> -> outcome_reviewer -> outcome_reviews.jsonl -> TrainingRow.v2 backlink ->
> bounded retest/shadow/promotion gates`. The first implementation slice exists:
> deterministic outcome routing, sanitized review packs, expanded outcome-review
> contract fields, training-row backlinks, and outcome-review recommendations that
> reuse the existing `feedback_followup` planner for bounded retests. The second
> slice adds `OutcomePromotionGate.v1`, a read-only gate view over accepted
> reviews, `shadow_forward`, `true_forward`, and `ready_strategy_catalog`. Next
> slices should add operator evidence reports and source-quality joins, still
> reusing existing `feedback_followup`, `setup_outcome_memory`, `shadow_forward`,
> and `true_forward` instead of adding a parallel queue or second memory system.

> **Update 2026-07-05 - headless product loop is the low-load operator mode.**
> The visible control room remains useful for manual observation, but the normal
> long-running product-paper mode on this machine should be
> `bat\paper_product_headless_loop.bat`: farm full-cycle only, no dashboard, no
> graph viewer, no status-monitor window, Telegram send off by default, bounded
> outcome-learning reviews on. If paper cards need to be delivered to active
> subscribers, use the explicit wrapper
> `bat\paper_product_headless_send_loop.bat`. This does not change live readiness:
> all paths remain `execution_allowed=false` and no old `main.py`/order path is
> revived.

> **Update 2026-07-04 - strict PFR calibration watch before live readiness.**
> Broad farm paper cards are useful for product visibility, but they are not live-ready
> and must not be promoted directly. The strict main-paper path stays gated by
> `ready_strategy_id + PAPER_FORWARD_READY`. To make that strict path observable rather
> than mostly empty, PFR-ready strategies may create paper-only pre-trigger watch cards
> when they are close to a validated breakout trigger. Those cards use explicit
> `entry_trigger=breakout_stop` semantics, so they do not fill as ordinary pullback
> limits. Exact PFR triggers keep priority; pre-trigger watches only fill unused paper
> capacity. This is calibration/observation, not live permission.

> **Update 2026-07-04 - product paper mode before live-main revival.**
> The next practical step is to operate the existing farm/PFR/main-paper chain as a
> visible paper product through `bat\paper_product_control_room.bat`, not through
> `start_all.bat` or old `main.py`. This gives continuous computation, validation,
> paper runtime observation, card preview, and status visibility while keeping
> `execution_allowed=false`. When the strict main-paper queue is empty, active farm
> paper candidates may now render as explicit paper Telegram cards, so subscribers can
> see research candidates while outcomes accumulate. A separate `main_paper_executor`
> contract remains the next deeper build for richer "what if opened" semantics; it must
> be reviewed before any old main-like execution behavior is revived.

> **Update 2026-07-03 - next work is not "more loop"; it is the safe main-paper
> executor.** The paper/research backbone is running and health-green. The gap is product
> semantics: the user expects a main-style paper executor that behaves like "what if we
> opened the trade", records the pseudo-trade and outcome, and produces readable
> subscriber cards. Current code only observes validator/PFR-backed paper rows through
> a strict `watch_paper` queue. Therefore the next roadmap item is to design and build a
> separate reviewed `main_paper_executor` / card contract that consumes only validated
> setups, uses shared deterministic math for levels and risk, allows LLM advice only
> through bounded schemas, writes trade/outcome/training rows, and keeps
> `execution_allowed=false`. Do not connect old live `main.py` directly.

> **Update 2026-06-27 - revival acceptance gate.** The current work is not to revive
> the old live engine. The active target is one visible paper/research operator cycle:
> farm -> validation/PFR -> paper signals -> main-paper instruction/consumer/runtime
> observation -> offline Telegram preview -> journal/training export. The machine
> gate is `operational_health.readiness.ready_for_visible_paper_research_loop`. It must
> pass before a long run is considered clean. `main.py`, `start_all.bat`, and
> `ws_scanner.py` remain legacy/diagnostic unless a separate reviewed paper-only port is
> built.

> **Update 2026-06-19 - canonical farm/paper loop.** The current center of
> `trading-bot-v2` is `farm_loop` over `farm_tasks.sqlite`, paper/research only:
> OKX universe intake -> data planning -> prepare/enrich -> sweeps -> classification
> into `unique_candidates` -> hard validation -> stamp-back -> `setup_library` cards
> -> gated `paper_loop` outcomes. The scanner is upstream intake, not the center.
> Visible full-cycle wrapper: `bat\strategy_lab_farm_full_cycle_loop.bat`.
> Current next work: richer paper promotion/demotion metrics, discovery ranking by
> movers, more GPU kernels, and a future microstructure provider.

> **Update 2026-06-18 — center shifted.** The current center of `trading-bot-v2` is the
> **universe-driven calculation farm** (paper/research only): a continuous research
> lifecycle (`farm_loop` → `farm_coordinator` → `farm_tasks.sqlite`) that grinds the OKX
> universe, fetches data (candles + public funding/OI), runs strategy sweeps, classifies,
> and hands candidates to honest validation. The **scanner below is now one upstream
> intake source**, not the primary product. Canonical:
> [docs/farm_loop_lifecycle.md](docs/farm_loop_lifecycle.md),
> [docs/farm_ownership_map.md](docs/farm_ownership_map.md),
> [docs/farm_runbook.md](docs/farm_runbook.md).
>
> ### Calculation Farm track (current direction)
> - Done: continuous lifecycle, fingerprint re-arm (no `already_queued` spin), public OI
>   loader (`NEEDS_OI_DATA` is now a managed data task), auto honest-validation stamp-back,
>   structured farm logs, bounded storage.
> - Next: microstructure provider (currently honest `NEEDS_MICRO_DATA`), discovery ranking by
>   movers, GPU kernels for more families.

The scanner track below remains valid but **secondary** (it feeds the farm).

The active scanner sub-system is an **info-edge scanner** for market events, plus a
paper-only confirmation bridge toward technical analysis. It is not an
auto-trading bot.

The scanner should:

- collect broad raw information;
- route events to the right asset and layer;
- distinguish expected / realized / context events;
- keep a full paper journal;
- measure outcomes against baselines;
- learn which sources, filters and event families are useful;
- surface only high-value `GO` / `WATCH` cards to Telegram.
- queue `WATCH/GO` events for later TA confirmation without allowing execution.

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
- Deep audit pass added scanner diagnostics and exposed the bad RED_FLAG
  over-escalation path.
- P0 scanner stabilization is implemented:
  - true `veto_flags` are separated from `no_edge_flags`;
  - chief errors retry visibly instead of silently becoming ordinary `NO_GO`;
  - outcome scoring is side-aware and marks beta-blind cases.
- P1 ingestion stabilization is implemented:
  - SEC EDGAR primary filing extraction;
  - Google News resolver throttling/backoff;
  - source-quality body/outcome metrics.
- Source onboarding is active:
  - one candidate source per layer;
  - `source_onboarding_report.py`;
  - quick rollback through `enabled: false`.
- Scanner-to-TA bridge v0 is implemented:
  - `watch_queue.jsonl` for `WATCH/GO`;
  - `setup_confirmation.confirm_setup()` status classifier;
  - paper-only invariant `execution_allowed=false`.

Known current problems:

- Fresh data is needed to verify that the veto/no-edge split reduces chief-rate
  without increasing missed idiosyncratic moves.
- Candidate sources need 24-48h measurement before keep/disable decisions.
- Per-asset `WATCH` synthesis is still missing; oil/energy can produce conflicting
  `WATCH` cards in both directions.
- `watch_queue` exists, but no runner consumes it and writes confirmation results.
- Macro/context headlines without one clean asset still need a separate context path.

## Near-Term Roadmap

### F1 - Calculation Farm (current core, primary track)

Goal: the universe-driven research farm is the active core; everything below (scanner
v0.6+) is now a **support track** that feeds it. Canonical:
[docs/farm_loop_lifecycle.md](docs/farm_loop_lifecycle.md).

Done:

- continuous self-deciding lifecycle (`farm_loop` → `farm_coordinator` → `farm_tasks.sqlite`);
- fingerprint-based re-arm — no `already_queued` spin; defer/block carry machine reasons;
- public keyless OI loader → `NEEDS_OI_DATA` is a managed data task that auto-unblocks
  `run_sweep` (microstructure stays an honest `NEEDS_MICRO_DATA`, no public provider);
- hard-validation export from `unique_candidates`, fingerprint-level stamp-back,
  automatic `setup_library` cards, minimal paper outcome feedback, structured farm
  logs, bounded storage.
- low-load headless product launcher over the canonical full-cycle loop, with
  bounded outcome-learning reviews enabled and dashboard/graph/status windows off.

Next:

- richer paper promotion/demotion metrics;
- accumulate headless paper-product cycles and review accepted outcome reviews
  before changing strategy parameters;
- discovery ranking by movers; GPU kernels for more families;
- manual-hypothesis intake channel (trader notes → structured spec → dry-run → farm task);
- microstructure data source (currently deferred).

### v0.6 - Calibration And Telegram Hygiene (scanner support track)

Goal: make the upstream scanner intake easier to judge and less noisy. The scanner is no
longer the project center; it is one intake source for the farm.

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
- done: separate true veto risk from no-edge/no-specificity reasons;
- done: add chief-error retry and visible unavailable state;
- done: side-aware outcome semantics and beta-blind marking.

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

### v0.7a - Scanner To TA Confirmation Contract

Goal: connect scanner watches to technical confirmation without reviving old Main
as a primary signal or creating any execution path.

Tasks:

- document the scanner-to-TA contract;
- backfill current `WATCH/GO` journal rows into `watch_queue`;
- add a paper-only runner that reads open watches and creates market/TA snapshots;
- call `confirm_setup()` and write a confirmation journal;
- keep `execution_allowed=false` through the entire path;
- keep old `ENTRY` signals from becoming orders or standalone trade signals.

Exit criteria:

- `WATCH/GO` rows can be classified as `WATCH_CONTINUE`, `SETUP_FORMING`,
  `TRADE_PLAN_READY`, `INVALIDATED`, `EXPIRED`, or `NEEDS_DATA`;
- every result is paper-only and auditable;
- no order path, Telegram execution path, or `AUTO_TRADE` path is touched.

### v0.8 - Source Quality And Intake Discipline

Goal: identify which sources deserve tokens and attention.

Tasks:

- refine source-quality dashboard/reporting;
- run `source_onboarding_report.py` after 24-48h of new candidate sources;
- compare RSS/aggregator/official/native feeds;
- identify sources that are mostly late recaps;
- identify sources that produce real watch candidates;
- tune layer-specific materiality thresholds conservatively.

Exit criteria:

- clear source ranking by layer;
- explicit keep / reduce / park decisions for each active source family.
- source candidates can be disabled quickly through registry config.

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
- a new execution-oriented `main_event_engine`;
- full GUI/SaaS wrapper;
- paid data integrations;
- Telegram account listener / Telethon production feed;
- broad multi-agent market-debate platform.

They may become relevant only after the scanner proves source quality, forward
measurement discipline, and paper-only TA confirmation quality.
