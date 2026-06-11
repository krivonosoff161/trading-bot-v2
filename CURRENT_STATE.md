# Current State

Updated: 2026-06-11

## Short Version

`trading-bot-v2` is currently a paper-only information-edge scanner with a
new read-only bridge toward technical confirmation.

The active system is still `src/scout/`. It collects market/news events, routes
them by asset and layer, uses cheap/chief LLM roles for structured review, writes
append-only records, and resolves outcomes later.

The old WebSocket Main/TA engines are not the primary signal source anymore.
They are frozen/reference code and may only be reused as a confirmation, risk,
level, or visualization layer behind scanner-led event candidates.

## Active Runtime

Primary scanner path:

```text
scanner.bat
  -> src/scout/scanner_v0.py --buffer --limit N
  -> data/scout/news_buffer.sqlite
  -> logs/scout/*.jsonl
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

Important active files:

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

After the scanner has run for 24-48h:

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
