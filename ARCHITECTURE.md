# Architecture

Updated: 2026-06-11

## Boundary

The project has two different contours:

1. **Active contour:** `src/scout/` information-edge scanner.
2. **Frozen/reference contour:** old WebSocket Main/TA and paper engines.

The scanner is the current product/research direction. Main/TA is not the source
of trade intent anymore. It may only confirm, invalidate, visualize, or risk-check
scanner-led candidates.

No current path may place orders from scanner output.

## Current Active Flow

```text
sources
  -> data/scout/news_buffer.sqlite
  -> machine_docs / normalized_events
  -> asset/layer router
  -> cheap layer agent
  -> orchestrator code gate
  -> chief model for selected candidates
  -> logs/scout/scanner_journal.jsonl
  -> logs/scout/watch_queue.jsonl
  -> setup_confirmation engine
  -> future paper confirmation journal
```

The scanner can send `GO/WATCH` cards to Telegram, but those are paper research
cards, not trade instructions.

## Scanner Components

| Component | File(s) | Role |
|---|---|---|
| Intake buffer | `src/scout/news_buffer.py` | Durable raw/extracted/normalized news state |
| Source registry | `src/scout/config/source_registry.yaml` | Source layer, trust, onboarding status, rollback |
| Router | `src/scout/router.py` | Asset/layer/baseline/source routing |
| Cheap layer agent | `src/scout/agents/layer_agent.py` | Structured first-pass extraction |
| Orchestrator | `src/scout/agents/orchestrator.py` | Deterministic escalation and safety gates |
| Chief | `src/scout/agents/chief.py` | Final candidate judgment |
| Journal | `src/scout/scanner_journal.py` | Append-only decision rows |
| Structured records | `src/scout/scanner_records.py` | Event/reasoning/training/memory blocks |
| Outcomes | `src/scout/resolve_outcomes.py` | Forward MFE/MAE, side-aware scoring, baseline/excess |
| Reports | `src/scout/*_report.py`, `scripts/analysis/*report.py` | Calibration, source quality, onboarding, deep audit |
| Watch queue | `src/scout/watch_queue.py` | `WATCH/GO` queue for later TA confirmation |

## Source Onboarding

The current source policy is deliberately conservative:

```text
one source per layer
  -> run 24-48h
  -> measure body quality, cards, chief cost, outcomes
  -> keep / observe / disable
```

Current source onboarding state:

| Layer | Source | State |
|---|---|---|
| L1 | `etf_flow` | Disabled context slot; needs provider or manual CSV |
| L2 | `token_unlocks` | Configured but needs `TOKENOMIST_API_KEY`; DexScreener quality is live context |
| L3 | `investing_commodities` | Candidate direct source |
| L4 | `rigzone` | Candidate direct source |
| L5 | `globenewswire_public` | Candidate direct company/IR source |

Rollback is one line: set `enabled: false` for a source in
`src/scout/config/source_registry.yaml`.

## Scanner To TA Confirmation

The bridge exists, but it is still paper/read-only:

```text
scanner_journal WATCH/GO
  -> watch_queue.jsonl
  -> setup_confirmation.confirm_setup(watch, SignalResult)
  -> status only
```

Allowed statuses:

- `WATCH_CONTINUE`
- `SETUP_FORMING`
- `TRADE_PLAN_READY`
- `INVALIDATED`
- `EXPIRED`
- `NEEDS_DATA`

Important invariants:

- `NO_GO` does not enter `watch_queue`.
- `watch_queue` rows have `execution_allowed=false`.
- `TRADE_PLAN_READY` is paper-only and also has `execution_allowed=false`.
- A future runner may write confirmation results, but must not call order
  execution.

## Main/TA Role

Old Main/TA research showed that the directed 15m Main is not a durable primary
signal. It can still provide useful market structure.

Allowed use:

- confirm scanner side with current market structure;
- invalidate scanner thesis when technical side conflicts;
- produce paper levels for analysis;
- provide chart/indicator context;
- classify `SETUP_FORMING` vs `WATCH_CONTINUE`;
- support later extended Telegram analysis.

Forbidden use:

- old `ENTRY` becoming a live order;
- old Main/TA originating trades without scanner event context;
- scanner calling old client text/entry formatters as a trade signal;
- any automatic execution from `watch_queue` or `setup_confirmation`.

## Frozen/Reference Code

These files are reference unless a task explicitly says otherwise:

- `src/strategy/signal.py`
- `src/strategy/signal_engine.py`
- `scripts/ws/ws_main_screener.py`
- `src/data/main_impulse_*.py`
- `src/data/impulse_pump_*.py`
- old `scripts/analysis/research/` experiments

Safe reuse from frozen code:

- `src/strategy/indicators.py`
- `src/strategy/chart_renderer.py`
- `src/strategy/signal_contract.py`
- `build_analysis_snapshot()` as read-only context
- OKX market-data utilities

## Operational Commands

Scanner:

```bash
bat\news_scanner_loop.bat
python -u src\scout\scanner_v0.py --buffer --limit 5
python -m src.scout.news_buffer stats
python src/scout/resolve_outcomes.py --limit 50
```

`bat\news_scanner_loop.bat` keeps the feedback loop closed by running
`resolve_outcomes.py --limit %SCANNER_OUTCOME_LIMIT%` after each scanner pass.
The resolver is bounded and keyless; it writes only mature forward outcomes.

Reports:

```bash
python src/scout/source_quality_report.py
python src/scout/chief_usage_report.py
python scripts/analysis/source_onboarding_report.py
python scripts/analysis/build_watch_queue.py --dry-run
```

Focused checks:

```bash
python -m pytest tests/test_source_onboarding.py tests/test_watch_queue.py tests/test_setup_confirmation.py tests/test_build_watch_queue.py -q
```

## Research machine (scanner -> farm -> validation -> feedback -> follow-ups)

The scanner is wired to the Strategy Lab farm and the honest-backtest validator
as one paper-only research loop. Each stage is bounded and individually safe; no
stage touches the order engine, `AUTO_TRADE`, or live trading.

```text
scanner_v0 --buffer        (writes logs/scout/watch_queue.jsonl; run separately, not by the demo)
resolve_outcomes           (scores mature forward outcomes; run by news_scanner_loop, not the demo)
  v
scanner WATCH/GO (logs/scout/watch_queue.jsonl)
  -> scanner_bridge        (src/research_lab/scanner_bridge.py)
  -> generate_event_sweeps --from-scanner   (bounded SweepSpec -> queue, missing_data is graceful)
  -> worker_once           (no-lookahead simulation on local candles)
  -> candidate registry    (private root)
  -> validate_candidates_pipeline  (export -> honest-backtest -> verdict -> feedback -> setup card)
  -> read_feedback         (verdicts -> farm recommendations; read-only)
  -> apply_feedback_recommendations  (NARROW_PARAMS -> bounded follow-up sweep; others -> notes)
  -> next research cycle
```

A bounded, visible, paper-only pass of the **farm -> validation -> feedback** half
(it seeds from the existing watch_queue; the fresh scanner pass + outcome
resolver are opt-in, default OFF):

```bash
bat\research_machine_demo_visible.bat
python -m scripts.strategy_lab.run_research_machine_demo --dry-run
python -m scripts.strategy_lab.run_research_machine_demo --run-scanner-pass --run-outcomes
```

The scanner only chooses which symbol to research; the news trigger is recorded
as provenance in the spec's `event_context` and is never used as a price anchor.
The sweep worker has a real backend contract (`cpu`/`gpu`/`auto`, see
`src/research_lab/gpu_runtime.py` + `gpu_kernels.py`): the `momentum_breakout`
signal kernel runs on a cupy GPU backend when one is usable (CPU/GPU parity
proven), `gpu` errors instead of silently using CPU when no backend is available,
and `auto` falls back to CPU with a recorded reason. The path-dependent trade
simulation stays CPU-only. Check with `python -m scripts.strategy_lab.gpu_doctor`.
Regime-filtered
follow-up sweeps are **not** implemented (`compile_sweep` does not forward filters);
`apply_feedback_recommendations` records `REGIME_SWEEP` as a note, not a queued
sweep. Strategy timeframe is recorded end-to-end (run evaluator derives it from
candle spacing; the exporter recovers it from the data-file label;
`repair_hard_validation_metadata` backfills legacy artifacts). Full operator
detail: [strategy_lab_operator_guide.md](docs/strategy_lab_operator_guide.md).
