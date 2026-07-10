# Current State

Updated: 2026-07-10

> **Update 2026-07-10 - formal paper acceptance harness.**
> `paper_acceptance_headless_loop.bat` records a private immutable baseline and
> runs the canonical headless farm with the local calculator/reviewer loop but
> without dashboards, graph viewers or Telegram network delivery. The acceptance
> report requires 24 hours, new lifecycle-v2 terminal evidence, exact account
> reconciliation, scenario closure/card evidence, clean lineage, retest progress,
> reconstructible chart history, bounded artifact growth and unchanged safety
> boundaries.

> **Update 2026-07-10 - canonical LLM invocation control.** The farm calculator
> now runs three sequential bounded roles on one allowlisted local `calculator-swarm`
> model: context classification, sweep-hypothesis proposal and hypothesis
> critique. All calculator/reviewer calls share a private invocation ledger with
> pre-call deduplication, role/provider circuit breaking and real token/cost
> metadata. Cloud providers cannot occupy the local calculator role; cloud
> reviewers still receive sanitized packets only. The obsolete write-only
> `local_llm_advisor` path was removed.
> A bounded local smoke completed all three roles; its critic rejected the
> proposed hypothesis, so no sweep was emitted. The private invocation aggregate
> recorded `9` calls, `6875` tokens and `0 RUB`, without storing raw prompts or
> granting execution authority.

> **Update 2026-07-10 - calibration evidence gate.** Adaptive geometry no
> longer learns from pre-cursor lifecycle rows. The private
> `trading_policy_calibration.json` report separates trusted
> `PaperSignalLifecycle.v2` terminal outcomes from legacy history, reports
> sample size, Wilson win-rate bounds, net/capture/give-back aggregates and a
> bounded profile verdict. Legacy rows remain available for forensics but cannot
> steer profile selection. Profile-wide `demote` can suppress a probe; all price
> levels remain deterministic and the gate has no execution authority.

> **Update 2026-07-10 - stable paper scenario lifecycle.** Active symbol-level
> trade theses retain one ID across confirmations and leader changes. Thesis
> events are append-only, and the preview emits a dedicated close card when no
> active signal remains. This changes paper observation/reporting only.

> **Update 2026-07-10 - unified paper lineage.** Farm/PFR provenance now survives
> the runtime and trade ledgers and is joined with account, scenario, Telegram,
> review and training IDs in a private derived index. Aggregate health exposes
> conflicts and missing downstream rows without exposing raw evidence.

> **Update 2026-07-10 - outcome retest return path.** Completed analyst-requested
> sweeps now produce deterministic result verdicts, rotate out of the pending
> catalog, update promotion-gate stages and enter setup memory with an explicit
> candidate/cell match scope. No result promotes itself to trading authority.

> **Update 2026-07-08 - validated PFR rows now lead main-paper queue.**
> The PFR/main-paper handoff no longer hides validated rows behind broad farm
> candidates in the runtime/Telegram preview priority order. `validated_pfr` queue
> items now receive a deterministic priority boost before ordinary
> `farm_calculated` rows. Verified after PR #139 on the private paper state:
> `main-paper instructions=31`, `queued=31`, `active_source={farm:29,pfr_farm:2}`,
> `validated_instructions=2`, `pfr_trigger_state=main_paper_has_pfr_instructions`,
> and `quality_action=collect_outcomes`. This is still paper/research only:
> `execution_allowed=false`, no `.env`, no `AUTO_TRADE`, no Telegram send authority,
> no old `main.py`, and no live order path.

> **Update 2026-07-08 - PFR trigger-budget and gap-memory alignment.**
> The PFR/main-paper handoff now has one visible fetch budget across the full-cycle
> launcher, `farm_loop`, standalone `paper_signals_run`, `cycle.run_cycle`, and
> `pfr_bridge.generate_pfr_signals`: `max_pfr_fetches=12`. The private aggregate
> product-quality report now separates a budget-limited PFR scan from an ordinary
> waiting-for-trigger state via `pfr_trigger_state=pfr_trigger_scan_limited`.
> PFR candidate selection also reuses recent private `pfr_gap_telemetry.jsonl`
> trigger-distance memory to check near-trigger validated setups before spending the
> bounded candle-fetch budget on farther historical winners. This is deterministic
> paper/research routing only: no `.env`, no `AUTO_TRADE`, no old `main.py`, no live
> orders, no Telegram sending authority, and no LLM authority over trade permission.

> **Update 2026-07-07 - adaptive farm geometry probes.**
> Broad farm paper signals no longer have to use only one fixed ATR/R geometry per
> family. The family builders still compute all entry/stop/TP/hold levels
> deterministically, but the paper-signal cycle can now add at most one bounded
> adaptive geometry profile next to the legacy `base` profile for the same
> symbol/timeframe/family. Outcome memory selects only profile labels such as
> `stop_relief`, `faster_capture`, or `runner_probe`; it never supplies raw prices,
> order fields, leverage, validator verdicts, or execution permission. The selected
> profile is written into `validator_context`, gate counts, and `TrainingRow.v2`
> fields (`farm_geometry_profile_id`, scales, reason), so later outcome analysis can
> compare whether the base or adaptive geometry actually performed better. The private
> `paper_product_quality_report` now also aggregates terminal rows by geometry profile,
> without copying raw signal text or private setup artifacts. This stays
> paper/research-only and does not touch `.env`, `AUTO_TRADE`, Telegram sending, old
> `main.py`, order paths, or private exchange endpoints.

> **Update 2026-07-05 - Telegram chart/news delivery hardening.**
> Paper setup chart delivery now requires a confirmed Telegram photo `message_id`;
> photo HTTP/`ok=false` failures no longer disappear as log-only warnings. The paper
> sender persists the sent-key ledger atomically after each successful card delivery,
> reducing duplicate sends after restarts. Scanner/news notifications are explicitly
> separated from subscriber paper cards: `TELEGRAM_NOTIFICATION_CHAT_ID` is the
> preferred public notification channel for `NEWS`/market/tokenomics-style items, with
> legacy `SCANNER_CHAT_ID` kept as fallback. Neither surface enables `AUTO_TRADE`,
> old `main.py`, order paths, private exchange endpoints, or public publication of full
> paper setup levels.

> **Update 2026-07-05 - low-load headless paper-product loop.**
> The project now has a low-load operator entrypoint:
> `bat\paper_product_headless_loop.bat`. It runs only the canonical
> `bat\strategy_lab_farm_full_cycle_loop.bat` with product cadence defaults and
> does not open the dashboard, graph viewer, or status-monitor window. Bounded
> outcome-learning reviews are enabled by default in this headless mode
> (`STRATEGY_LAB_RUN_AGENT_ROLE_REVIEWS=1`, small per-cycle caps), but they remain
> advisory: no `.env` edits, no `AUTO_TRADE`, no old `main.py`, no orders, no
> private exchange endpoints, and no LLM authority over trade levels or execution.
> Telegram delivery stays off by default; `bat\paper_product_headless_send_loop.bat`
> is the explicit opt-in wrapper for sending reviewed paper cards to active
> subscribers while keeping the same paper-only boundaries.

> **Update 2026-07-05 - outcome-learning loop starts closing the feedback cycle.**
> Terminal paper outcomes now have a deterministic `OutcomeLearningCase.v1`
> review layer. `agent_role_review_cycle` sends sanitized outcome cases to the
> existing bounded `outcome_reviewer` role, and later `TrainingRow.v2` exports can
> link the accepted advisory review back through `outcome_review_id`,
> `outcome_learning_review_kind`, `outcome_learning_bucket`, and
> `outcome_learning_actionability`. Accepted outcome reviews can also compile into
> farm-level `Recommendation` objects and reuse the existing `feedback_followup`
> path for bounded retest sweeps or no-queue notes. This is still paper/research only:
> `execution_allowed=false`, no `.env`, no private exchange endpoints, no Telegram
> send path, and no LLM authority over entry/stop/TP/side/validator verdict.
> The second slice adds `OutcomePromotionGate.v1`: accepted reviews are now
> classified into `review_only`, `needs_retest`, `needs_shadow`,
> `needs_true_forward`, `collect_true_forward`, `operator_review_only`, or
> `eligible_for_operator_review`. This is a status/explanation gate only; even
> a mature true-forward row plus ready-catalog evidence remains
> `execution_allowed=false` and needs an operator decision.
> See [docs/outcome_learning_loop_2026-07-05.md](docs/outcome_learning_loop_2026-07-05.md).

> **Update 2026-07-04 - paper-product operator entrypoint.**
> The current user-facing paper mode is now explicit:
> `bat\paper_product_control_room.bat`. It wraps the canonical Strategy Lab control
> room with product cadence defaults (`farm=180s`, `status=120s`) and keeps Telegram
> network sends off by default. If reviewed paper cards should be delivered to active
> subscribers, use the separate explicit wrapper
> `bat\paper_product_control_room_send.bat`; this only enables the downstream paper
> Telegram sender and still does not enable `AUTO_TRADE`, old `main.py`, order paths,
> or private exchange endpoints. `python scripts/project_snapshot.py` now also prints
> a `PAPER PRODUCT` line over private aggregate snapshots, so a quiet old
> `MAIN SIGNALS` log no longer hides an active farm/PFR/main-paper paper chain.
>
> **Update 2026-07-04 - active farm candidate Telegram fallback.**
> The paper Telegram preview now has a product fallback: if the strict
> `main_paper_trades` and `main_paper_consumed` artifacts are empty, active
> `paper_signals.json` rows with `status=opened_paper` or `status=armed` render as
> subscriber-facing paper candidate cards. The fallback keeps the same boundaries:
> `paper_only=true`, `execution_allowed=false`, no old `main.py`, no order path, and
> no Telegram network call during preview. On the current private Strategy Lab state
> the fallback rendered `8` cards with `invalid=0`; dry-run sender reported
> `eligible=8`, `targets=4`, `sends_network=false`.

> **Update 2026-07-03 - running, but only as paper/research backbone.**
> The canonical visible farm loop is alive (`python pid=18900`, stage `sleep`) on
> `feature/calc-farm` at `7bbc65c feat: harden farm loop observability`.
> `operational_health` has no blocking gates and reports the visible
> farm/PFR/paper/main-paper/journal cycle as assembled. The verified mode is still
> `paper_only=true`, `execution_allowed=false`, `AUTO_TRADE=false`. The current chain
> is validator-backed paper observation, not old-main live execution:
> `farm_loop -> validation/PFR -> paper_signals -> main_paper_bridge ->
> main_paper_consumer -> main_adaptive_policy -> main_paper_runtime_queue ->
> main_paper_runtime_observation -> main_paper_trade_ledger -> paper_account_ledger
> -> Telegram preview/audit
> -> training export -> journal`. See
> [docs/session_handoff_2026-07-03.md](docs/session_handoff_2026-07-03.md).
>
> **Important mismatch:** the user expected a main-style "what if we opened this?"
> paper executor that makes forecasts, writes pseudo-trades, and later records outcomes.
> The strict main-paper watch queue still accepts only validator/PFR-backed rows with a
> ready strategy id. For product visibility, active farm paper candidates can now be
> rendered into explicit paper Telegram cards when that strict queue is empty. The deeper
> next product step remains a reviewed `main_paper_executor` contract, not wiring old
> `main.py` into the farm.

> **Update 2026-06-27 - visible paper/research loop acceptance.**
> `operational_health` now has a single aggregate gate:
> `ready_for_visible_paper_research_loop`. It passes only when the canonical visible
> launch scripts exist, `AUTO_TRADE` is off, old live `main.py` is isolated, PFR exists,
> the paper chain is non-empty and clean, the public-candle paper runtime has observed
> the queue without invalid/provider errors, the Excel journal exists, and a training
> export exists. Telegram ownership is now explicit through `telegram_delivery_flow`:
> farm core does not send Telegram, paper alerts are preview-only by default, scanner
> and analyzer Telegram surfaces are separate operator surfaces, and legacy
> `ws_scanner.py` remains diagnostic. See
> [docs/farm_notification_layer.md](docs/farm_notification_layer.md),
> [docs/farm_ownership_map.md](docs/farm_ownership_map.md), and
> [docs/farm_runbook.md](docs/farm_runbook.md).

> **Update 2026-06-26 - paper-trading operational preflight.**
> The farm/PFR/paper-signal path was checked end-to-end in bounded paper/research mode.
> `operational_health` now reports Telegram/LLM/journal/PFR readiness without exposing
> secrets; `paper_signals_run` and `farm_loop --run-paper-signals` have public fetch
> timeouts plus PFR/observe caps for fast smoke checks; the visible full-cycle wrapper
> now passes the PFR DB path explicitly, so farm/PFR/paper-watch runs as one operator
> loop; active paper-watch signals are exported into a main-readable paper instruction
> view with `execution_allowed=false`; that view is then validated into a paper-only
> main consumer audit artifact (`state/derived/main_paper_consumed.jsonl`) before any
> future executor; accepted instructions are also materialized into a main-compatible
> paper runtime queue (`state/derived/main_paper_runtime_queue.jsonl`) with
> `runtime_action=watch_paper`, `execution_allowed=false`, and full paper-observation
> context (`entry_zone`, `boundary_ts`, expiry, hold bars, risk, fingerprint, dedup key,
> source mode, exit mode); `farm_loop --run-paper-signals` observes that queue through
> `scripts.strategy_lab.main_paper_runtime` into
> `state/derived/main_paper_runtime_observation.jsonl` using public candles only;
> then instructions are rendered into
> offline Telegram preview cards (`state/derived/paper_telegram_preview.jsonl`) with no
> send/network path;
> terminal paper-watch outcomes can be exported to `state/derived/paper_signal_training.jsonl`;
> `scripts/build_journal.py` now surfaces that export in a `Paper Watch` sheet while
> still skipping private OKX fills unless `JOURNAL_ENABLE_PRIVATE_FILLS=1` is explicitly
> set. `operational_health` now reports paper-chain counts, not only file presence
> (`instructions -> accepted/rejected -> runtime queue -> runtime observation -> Telegram preview`), so the
> operator can see exactly where a chain breaks. Verified state:
> Alibaba scanner LLM configured, default/scanner Telegram configured, paper Telegram not
> configured, PFR DB present, bounded smoke completed without live/money paths. See
> [docs/paper_trading_operational_audit_2026-06-26.md](docs/paper_trading_operational_audit_2026-06-26.md).

> **Update 2026-06-20 — Phase 0+1 hardening/search-quality done; disciplinary gate = (B) sub-cost.**
> Phase 0 (reproducible fail-loud validation, off-by-default stage visibility, discovery
> freshness, dashboard observability, honest OI gate, recursive money-path guard, legacy-loop
> abort) and Phase 1 (real per-family stop/take/hold grids + tiers, multiple-testing
> correction, revived REGIME_SWEEP follow-ups, GPU benchmark = CPU wins → no new kernels) are
> merged on `feature/calc-farm` (full suite 1329 passed). A bounded full-loop gate-run on real
> public OKX data produced **0 fresh `PAPER_FORWARD_READY` of 84 validations; `FAILED_COSTS`
> 76–83%** → verdict **(B): the current search space is sub-cost / fragile**. P2 (Stage 6b
> rejected-mining, Stage 8 true forward paper) stays closed; the next move is a constraint
> change (data/regime/class/execution), not more pipeline. See
> [docs/gate_verdict_phase1_2026-06-20.md](docs/gate_verdict_phase1_2026-06-20.md).

## Short Version

`trading-bot-v2`'s current center is the **universe-driven calculation farm**
(paper/research only). The farm runs a continuous, self-deciding research lifecycle —
`farm_loop` → `farm_coordinator` over `farm_tasks.sqlite` — that grinds the OKX universe,
fetches missing data (candles + public funding/OI), runs strategy sweeps, classifies
results, hands promising candidates to honest validation, writes setup cards, and can feed
the gated paper runtime from `paper_forward_ready` cards. See
[docs/farm_loop_lifecycle.md](docs/farm_loop_lifecycle.md) and
[docs/farm_runbook.md](docs/farm_runbook.md).

The **scanner (`src/scout/`) is now one upstream intake source**, not the project center:
its `WATCH/GO` rows feed the farm via `intake_adapter` (read from the watch file). It still
collects/routes/reviews market+news events and resolves outcomes, but the farm — not the
scanner — is the main engine, and the farm is universe-driven, not news-driven.

The old WebSocket Main/TA engines remain frozen/reference (confirmation/risk/level context
only); their useful strategy logic is already ported into research_lab families
(see [docs/farm_ownership_map.md](docs/farm_ownership_map.md)).

Portfolio-level documentation authority and public/private storage rules live in
the [Documentation Contract](https://github.com/krivonosoff161/krivonosoff161/blob/main/docs/documentation-contract.md).
This file is the current state for this repository; it does not publish private
strategy calculations or raw runtime evidence.

## Active Runtime

Primary farm path (current center):

```text
bat\strategy_lab_farm_full_cycle_loop.bat
  (or farm_loop --apply --run-worker --run-validation --run-paper --enrich-funding --enrich-oi)
  -> farm_coordinator.run_coordinator_cycle   (brain: state/farm_tasks.sqlite)
  -> materializes run_sweep -> state/strategy_lab.sqlite (compute queue) -> worker
  -> classify -> unique_candidates -> honest validation -> stamp-back -> setup_library
  -> paper_loop (only paper_forward_ready cards) -> paper outcomes
  -> paper_signals/PFR watch -> main_paper_instructions -> main_paper_consumed
     -> main_paper_runtime_queue
     -> main_paper_runtime_observation
     -> paper_telegram_preview
        (strict main-paper cards first; active farm candidate cards only as paper fallback)
     -> paper_signal_training export -> scripts/journal.xlsx / Paper Watch sheet
     (paper-only contract audit, not consumed by live main)
  -> logs/farm/{cycle_log,task_transitions,errors}.jsonl
```

Fast wiring smoke exists for the derived paper chain: `farm_loop --once --apply` with
`--true-forward-max-candidates 0 --paper-signals-max-new 0 --paper-signals-max-observe 0`.
It intentionally leaves worker/validation/paper stages off and only proves
farm -> paper-watch -> main instruction -> runtime queue -> runtime observation -> preview wiring.

Upstream scanner intake source (feeds the farm, no longer the center):

```text
scanner.bat
  -> src/scout/scanner_v0.py --buffer --limit N
  -> data/scout/news_buffer.sqlite
  -> logs/scout/watch_queue.jsonl   (consumed by farm intake_adapter)
```

Current event-to-confirmation path:

```text
sources
  -> news_buffer raw_items / machine_docs / normalized_events
  -> layer cheap agent
  -> orchestrator gate
  -> chief model for selected candidates
  -> scanner_journal.jsonl
  -> watch_queue.jsonl
  -> setup_confirmation engine
```

Important active files — calculation farm (current core):

- `scripts/strategy_lab/farm_loop.py` - the continuous research-cycle CLI (active core).
- `src/research_lab/farm_coordinator.py` - the cycle brain (intake→plan→prepare/enrich→sweep→classify→validate→pivot).
- `src/research_lab/farm_tasks_db.py` - `state/farm_tasks.sqlite` typed-task lifecycle.
- `src/research_lab/data_planner.py` - decide-before-compute (prepare/defer/enrich/block-with-reason).
- `src/research_lab/providers/okx_flow.py` - keyless public funding + open-interest loaders.
- `src/research_lab/validation_orchestrator.py` - export→honest-backtest→stamp-back.
- `src/research_lab/setup_library.py` - hard-validated setup card writer.
- `scripts/strategy_lab/paper_loop.py` - gated paper runtime over ready setup cards.
- `src/research_lab/farm_journal.py` - structured cycle/transition/error logs.
- `scripts/strategy_lab/farm_status_report.py` - operator picture. Use `--fast`
  for visible status monitors; run the full report without `--fast` only for manual
  audit/drilldown because it rebuilds heavier derived research views.
- `bat/paper_product_control_room.bat` - preferred product-facing paper launcher:
  farm/PFR/main-paper/status with faster visible cadence and Telegram dry-run by
  default.
- `bat/paper_product_control_room_send.bat` - explicit opt-in wrapper for sending
  already validated paper Telegram cards to active subscribers.
- `bat/paper_product_headless_loop.bat` - low-load product paper launcher: farm
  full-cycle only, no dashboard/graph/status windows, bounded outcome-learning
  reviews enabled, Telegram delivery off by default.
- `bat/paper_product_headless_send_loop.bat` - explicit opt-in headless wrapper
  for sending reviewed paper Telegram cards to active subscribers.
- `bat/strategy_lab_farm_full_cycle_loop.bat` - visible full-cycle operator wrapper.
- `bat/strategy_lab_control_room.bat` - visible control room for farm loop,
  dashboard, private graph viewer, and status monitor windows.
- `bat/strategy_lab_farm_full_cycle_stop.bat` - clean stop-file wrapper for that loop.

Important active files — scanner (upstream intake, second level):

- `src/scout/scanner_v0.py` - scanner runtime.
- `src/scout/news_buffer.py` - SQLite intake buffer.
- `src/scout/router.py` - asset/layer/baseline routing.
- `src/scout/agents/layer_agent.py` - cheap fact extraction.
- `src/scout/agents/orchestrator.py` - code rules and chief escalation.
- `src/scout/agents/chief.py` - final `GO / NO_GO / WATCH` model.
- `src/scout/watch_queue.py` - idempotent `WATCH/GO` queue for later TA confirmation.
- `src/strategy/setup_confirmation.py` - pure paper-only confirmation classifier.
- `src/scout/resolve_outcomes.py` - forward outcome scoring.
- `src/scout/source_quality_report.py` - source/routing report.
- `src/scout/chief_usage_report.py` - chief-call/cost report.
- `scripts/analysis/source_onboarding_report.py` - 24-48h source onboarding report.
- `scripts/analysis/build_watch_queue.py` - backfill `watch_queue` from existing journal.

## What Changed On 2026-06-11

Scanner stabilization and ingestion:

- `red_flags` were split into true `veto_flags` vs `no_edge_flags`; lack of
  specificity no longer escalates to chief as a risk veto.
- Chief errors now retry visibly instead of silently becoming ordinary `NO_GO`.
- Outcome scoring is side-aware for `WATCH`; beta-blind self-baseline cases are
  marked instead of pretending excess is zero.
- SEC EDGAR now extracts primary filing bodies and metadata instead of title-only
  snippets.
- Google News resolver has polite throttling/backoff and soft fallback on 429.
- DexScreener quality metrics are available as L2 context.
- ETF-flow, token-unlock, and EIA surprise interfaces exist, with honest disabled
  behavior when provider/API keys are absent.

Source onboarding:

- The current source experiment is "one new source per layer, then measure".
- `investing_commodities`, `rigzone`, and `globenewswire_public` are candidate
  direct sources.
- `etf_flow` is disabled until a provider or manual CSV is configured.
- `token_unlocks` is present but needs `TOKENOMIST_API_KEY`.
- Rollback for any source is `enabled: false` in
  `src/scout/config/source_registry.yaml`.

Scanner to Main/TA bridge:

- `WATCH` and `GO` scanner rows are written to `logs/scout/watch_queue.jsonl`.
- `NO_GO` is never queued for TA confirmation.
- Queue rows set `confirm_required=true` and `execution_allowed=false`.
- `confirm_setup()` can return `TRADE_PLAN_READY`, but that is paper-only and
  still has `execution_allowed=false`.

## Current Diagnosis

The scanner is operational, but not yet calibrated enough to be treated as a
trading engine.

Current evidence:

- Telegram gating is behaving correctly: `GO/WATCH` only by default; `NO_GO`
  remains in logs unless `SCANNER_SEND_NO_GO=true`.
- The old chief over-escalation path was found and fixed, but it needs 1-2 days
  of fresh data to verify that chief-rate falls toward the expected range.
- New direct sources need 24-48h measurement before keep/disable decisions.
- Main/TA research verdict is clear: old directed 15m Main is weak as a primary
  edge and should be used only as confirmation/risk context.

## Immediate Next Checks

Farm (current core) — after a bounded cycle:

```bash
python -m scripts.strategy_lab.farm_loop --once --dry-run          # plan only, writes nothing
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --enrich-oi
bat\paper_product_control_room.bat                                # preferred visible paper-product mode; Telegram dry-run
bat\paper_product_control_room_send.bat                           # explicit subscriber paper-card send mode
bat\strategy_lab_farm_full_cycle_loop.bat                         # visible continuous farm/validation/paper/PFR-watch loop
bat\strategy_lab_control_room.bat                                 # visible farm + dashboard + graph + status windows
python scripts/project_snapshot.py                                # quick git/process/main + paper-product aggregate snapshot
python -m scripts.strategy_lab.farm_status_report --fast           # visible status: tasks, blocked/deferred, paper/PFR/main-paper
python -m scripts.strategy_lab.farm_status_report                  # full audit/drilldown; slower on a large private DB
```

Scanner intake (second level) — after the scanner has run for 24-48h:

```bash
python scripts/analysis/source_onboarding_report.py
python src/scout/chief_usage_report.py
python src/scout/source_quality_report.py
python scripts/analysis/build_watch_queue.py --dry-run
```

Expected questions:

- Did chief-rate fall after the `veto_flags/no_edge_flags` split?
- Are candidate sources producing full bodies or title-only junk?
- Which sources should be kept, observed, or disabled?
- How many `WATCH/GO` rows are available for TA confirmation?

## Boundaries

Do not touch without explicit user approval:

- live order execution;
- `AUTO_TRADE`;
- real-money paths;
- `.env` secrets;
- OKX live trading config;
- Telegram credentials or target channels;
- old frozen engines as primary signal generators.

Safe current work:

- scanner docs/tests/reports;
- source onboarding measurement;
- watch queue / setup confirmation reporting;
- paper-only TA confirmation runner;
- extended analysis by button or offline command.
