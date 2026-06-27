# Architecture

Updated: 2026-06-27

## Boundary

> **Update 2026-06-27 - paper/main/Telegram ownership.** The active architecture now
> has a rebuildable paper handoff after the farm: paper signals/PFR ->
> `main_paper_instructions` -> `main_paper_consumed` -> `main_paper_runtime_queue` ->
> public-candle `main_paper_runtime_observation` -> offline `paper_telegram_preview` ->
> training/journal export. This is still paper/research only. The old live `main.py`
> does not consume farm instructions, and Telegram is a surface, not an executor.
> `operational_health.readiness.ready_for_visible_paper_research_loop` is the aggregate
> machine-check for this operator state.

> **Update 2026-06-19 — center shifted to the calculation farm.** The current
> research center is the **universe-driven calculation farm** (`farm_loop` →
> `farm_coordinator` → `farm_tasks.sqlite`, paper/research only). The scanner
> (`src/scout/`) is now one **upstream intake source** that feeds the farm, not the
> product. Canonical: [docs/farm_loop_lifecycle.md](docs/farm_loop_lifecycle.md),
> [docs/farm_ownership_map.md](docs/farm_ownership_map.md).

The project has three contours:

1. **Active research core:** the calculation farm (`research_lab` + `scripts/strategy_lab`).
2. **Upstream intake:** `src/scout/` information-edge scanner (one farm intake source).
3. **Frozen/reference:** old WebSocket Main/TA and paper engines (confirmation/risk/level
   context only; their useful strategy logic is already ported into research_lab families).

Neither the scanner nor the farm may place orders. The farm is fully isolated from the
money path (no `.env`/`AUTO_TRADE`/orders/private endpoints/Telegram), enforced by an AST
import test over the farm modules.

## Current Active Flow (calculation farm)

The active core is the universe-driven calculation farm. Full detail:
[docs/farm_loop_lifecycle.md](docs/farm_loop_lifecycle.md).

```text
intake (scanner watch_queue.jsonl via intake_adapter + OKX discovery snapshot)
  -> data_planner            (per symbol/tf: prepare / defer / enrich / block-with-reason)
  -> farm_coordinator        (brain: state/farm_tasks.sqlite, typed tasks, fingerprint re-arm)
       prepare_data (public candles) / enrich_funding / enrich_oi (public OKX, keyless)
  -> run_sweep               (materialized into state/strategy_lab.sqlite compute queue)
  -> worker_once             (no-lookahead simulation; cpu/gpu/auto)
  -> classify_result         (-> unique_candidates)
  -> validation_orchestrator (unique_candidates -> export -> honest-backtest -> STAMP-BACK
                              -> setup_library)   [--run-validation]
  -> paper_loop              (only paper_forward_ready setup cards -> paper outcomes)
  -> pivot (work_available / advanced_lifecycle / discovery_refill / blocked:no_eligible_tasks)
  -> logs/farm/{cycle_log,task_transitions,errors}.jsonl
```

## Scanner Intake Flow (upstream source, not the center)

```text
sources
  -> data/scout/news_buffer.sqlite
  -> machine_docs / normalized_events
  -> asset/layer router
  -> cheap layer agent
  -> orchestrator code gate
  -> chief model for selected candidates
  -> logs/scout/scanner_journal.jsonl
  -> logs/scout/watch_queue.jsonl        (consumed by the farm via intake_adapter)
  -> setup_confirmation engine
  -> future paper confirmation journal
```

The scanner can send `GO/WATCH` cards to Telegram, but those are paper research
cards, not trade instructions. Strategy Lab paper alerts are currently offline preview
artifacts by default; scanner/analyzer Telegram surfaces are separate operator surfaces,
not farm/PFR executors. See
[docs/farm_notification_layer.md](docs/farm_notification_layer.md).

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

## Research machine — the calculation farm (current core)

The calculation farm is the continuous, self-deciding research lifecycle. It is
universe-driven (scanner watches are one optional intake source) and fully paper/research
only — no stage touches the order engine, `AUTO_TRADE`, or live trading. Canonical doc:
[docs/farm_loop_lifecycle.md](docs/farm_loop_lifecycle.md).

```text
intake (scanner watch_queue.jsonl via intake_adapter + OKX discovery)
  -> data_planner          (per symbol/timeframe: prepare / defer / enrich / block-with-reason)
  -> farm_coordinator      (brain: state/farm_tasks.sqlite — typed tasks, fingerprint re-arm)
       prepare_data        (public OKX candles) / enrich_funding / enrich_oi (public OKX)
  -> run_sweep             (materialized into state/strategy_lab.sqlite compute queue)
  -> worker_once           (no-lookahead simulation on local candles; cpu/gpu/auto)
  -> classify_result       (-> unique_candidates, keyed symbol+tf+family+params+fingerprint)
  -> validation_orchestrator (export -> honest-backtest -> verdict -> STAMP-BACK)  [--run-validation]
  -> pivot                 (work_available / advanced_lifecycle / discovery_refill /
                            blocked:no_eligible_tasks — never spins on already_queued)
  -> logs/farm/{cycle_log,task_transitions,errors}.jsonl
```

Run it (dry-run writes nothing):

```bash
python -m scripts.strategy_lab.farm_loop --once --dry-run
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --enrich-funding --enrich-oi
python -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation --run-paper --stop-file STOP
bat\strategy_lab_farm_full_cycle_loop.bat
bat\strategy_lab_control_room.bat
bat\strategy_lab_farm_full_cycle_stop.bat
python -m scripts.strategy_lab.farm_status_report --fast
python -m scripts.strategy_lab.farm_status_report   # full audit/drilldown
```

**Legacy demo (superseded):** the older flat bridge path
`scanner_bridge -> generate_event_sweeps --from-scanner -> worker_once ->
validate_candidates_pipeline` (driven by `run_research_machine_demo.py`) predates the
coordinator and is kept only as a walkthrough — see
[docs/farm_ownership_map.md](docs/farm_ownership_map.md).

The scanner only chooses which symbol to research; the news trigger is recorded
as provenance in the spec's `event_context` and is never used as a price anchor.
The sweep worker has a real backend contract (`cpu`/`gpu`/`auto`, see
`src/research_lab/gpu_runtime.py` + `gpu_kernels.py` + `gpu_simulator.py`) with
two independently GPU-accelerated stages: the `momentum_breakout` signal kernel
and the batched trade simulation (first-touch SL/TP/max-hold barrier, long &
short, fees+slippage) for the supported exit mode. Both run on a cupy GPU backend
when usable (CPU/GPU parity proven by tests); `gpu` errors instead of silently
using CPU when no backend is available, and `auto` falls back to CPU with a
recorded reason. Unsupported exit modes (e.g. trailing stops) and over-cap
batches fall back to CPU with an explicit `simulation_fallback_reason`; the
`signal_backend` and `simulation_backend` are recorded separately in
`metrics.json.runtime`. Check with `python -m scripts.strategy_lab.gpu_doctor`.
Regime-filtered
follow-up sweeps are **not** implemented (`compile_sweep` does not forward filters);
`apply_feedback_recommendations` records `REGIME_SWEEP` as a note, not a queued
sweep. Strategy timeframe is recorded end-to-end (run evaluator derives it from
candle spacing; the exporter recovers it from the data-file label;
`repair_hard_validation_metadata` backfills legacy artifacts). Full operator
detail: [strategy_lab_operator_guide.md](docs/strategy_lab_operator_guide.md).
