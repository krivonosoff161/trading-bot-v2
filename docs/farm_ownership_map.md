# Farm Ownership Map

Status: **ACTIVE**. Last updated: 2026-06-19.

Purpose: after `farm_loop` + `farm_tasks.sqlite` became the calculation farm's core,
this map records which loops are active, diagnostic, or legacy. Nothing here gives the
farm permission to touch the old money path.

## Databases

| DB | Role |
|----|------|
| `state/farm_tasks.sqlite` | **Brain**: typed lifecycle, scheduling, reasons, fingerprints. |
| `state/strategy_lab.sqlite` | **Compute queue**: proven sweep queue drained by the worker. |
| `state/scanner_farm_loop.sqlite` | Legacy checkpoint for the superseded scanner-farm loop. |

## Ownership Matrix

| Entry point | Role | Notes |
|---|---|---|
| `scripts/strategy_lab/farm_loop.py` | **CORE** | Current continuous self-deciding lifecycle. |
| `bat\strategy_lab_farm_full_cycle_loop.bat` | **CORE WRAPPER** | Visible operator path: farm -> worker -> validation -> paper. |
| `bat\strategy_lab_farm_full_cycle_stop.bat` | **CORE WRAPPER** | Clean stop-file for the wrapper above. |
| `scripts/strategy_lab/worker_once.py` | **CORE EXECUTOR** | Single-job compute executor. Must not delete. |
| `scripts/strategy_lab/worker_loop.py` | **KEEP / OFF DEFAULT** | Standalone compute daemon; not a lifecycle brain. |
| `scripts/strategy_lab/scanner_farm_loop.py` | **ARCHIVE-LEGACY** | Flat scanner-watch -> sweep queue path; superseded by the brain. `main()` aborts unless `--i-understand-legacy` (0.7), so it cannot accidentally double-queue alongside `farm_loop`. |
| `scripts/strategy_lab/universe_farm_loop.py` | **ARCHIVE-LEGACY** | Cursor-based universe grind; absorbed by `discovery_refill`. `main()` aborts unless `--i-understand-legacy` (0.7). |
| `scripts/strategy_lab/research_loop.py` / `research_cycle.py` / `research_session.py` | **ADVISORY LANE** | LLM proposal/review lane. The model is a JSON-only hypothesis advisor, not the farm controller. |
| `src/research_lab/feedback_followup.py` | **CORE PLANNER** | Deterministic bounded follow-up planner; consumed by `farm_loop` via `schedule_followup`. |
| `scripts/strategy_lab/generate_event_sweeps.py` | **KEEP / OFF DEFAULT** | Price-event sweep generator; `--from-scanner` is legacy bridge. |
| `scripts/strategy_lab/apply_feedback_recommendations.py` | **KEEP / MANUAL DIAGNOSTIC** | Manual follow-up bridge; canonical automation now goes through `farm_loop`. |
| `scripts/strategy_lab/autopilot_once.py` | **KEEP / OFF DEFAULT** | Registry/spec/queue filler; superseded by lifecycle follow-up logic. |
| `scripts/strategy_lab/requeue_stale_jobs.py` | **KEEP / MAINTENANCE** | Manual stale-job recovery. |
| `scripts/strategy_lab/sync_state_db.py` | **KEEP / REPAIR** | Imports completed run dirs if worker import crashed. |

## Main Engine Boundary

The old live/paper runners are closed as trading engines. They must not be imported, run,
or wired into farm/paper/operator paths. Useful math can be copied or re-derived inside
`research_lab`, then tested through the farm.

Forbidden as farm imports:

- `main.py`
- `scripts/auto_execute.py`
- `scripts/run_latest_analysis.py`
- `src/exchange/okx_client.py`
- `src/data/*_engine.py`
- Telegram modules / credentials
- `.env` / config money path

This is enforced by the farm boundary tests.

## Strategy Logic: Closed Engine, Open Hypothesis

| Pattern | Baseline in lab | Still-open research hypotheses |
|---|---|---|
| BB / volume fade | `strategies/bb_fade.py` | `not_thrust`, `slope_fading`, entry-quality filters. |
| FVG | `strategies/fvg_family.py`, `features/fvg.py` | Gap quality, mitigation depth, displacement variants. |
| Fractal / swing | `strategies/fractal_family.py`, `features/structure.py` | Sweep/retest geometry, multi-pivot structure. |
| Pump / impulse | `strategies/pump_dump.py` | Event detectors, MFE/MAE, pair-risk, continuation geometry. |
| Main `compute_signal` | `strategies/regime_family.py` | Regime labels, lag/freshness, DRIFT both-side behavior, late-entry filters. |

"Ported" means a baseline seed exists, not that the idea is exhausted.

## Manual Research Intake

Trader notes, screenshots, and manual calculations are first-class hypothesis sources,
but they never promote directly to candidates/main.

```text
manual note / screenshot / calc
  -> structured hypothesis card / spec
  -> dry-run validation
  -> farm research task
  -> classify -> honest validation -> setup card
  -> paper only if PAPER_FORWARD_READY
```

## LLM Calculator Boundary

The weak/local LLM calculator is advisory only:

- may review aggregate packs and propose draft hypotheses;
- must return bounded JSON;
- cannot start/stop processes;
- cannot enqueue outside deterministic validation gates;
- cannot promote paper/live status;
- cannot touch `.env`, order paths, Telegram, or old main engine.

Code and validators decide what enters the farm.

LLM proposals pass through the same parameter authority as human/CLI proposals:
unknown keys, wrong ranges, missing executable exits, or reward/risk below 1:2 are
rejected. Repeated contract failures are counted and should disable the model for the
current run rather than letting it steer the farm.
