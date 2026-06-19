# Farm Ownership Map — who owns what (loops, engines, duplicates)

Status: **ACTIVE** · Last updated: 2026-06-18

Purpose: after the continuous lifecycle (`farm_loop` + `farm_tasks.sqlite`) became the
calculation farm's core, this map records the role of every calculation/scheduler/worker
loop so we do not keep parallel duplicators with no defined role. Nothing is deleted in
this layer; roles are assigned and legacy loops are labelled. Main trading engine is
untouched.

## Databases

| DB | Role |
|----|------|
| `state/farm_tasks.sqlite` | **BRAIN** — typed-task lifecycle (NEW; the only writer). |
| `state/strategy_lab.sqlite` | **COMPUTE QUEUE** — the proven sweep queue the worker drains. |
| `state/scanner_farm_loop.sqlite` | legacy checkpoint for the superseded scanner_farm_loop. |

## Ownership matrix

| Loop / entrypoint | Role | Notes |
|---|---|---|
| `scripts/strategy_lab/farm_loop.py` → `farm_coordinator.run_coordinator_cycle` | **CORE** | The continuous self-deciding lifecycle. The reference. |
| `scripts/strategy_lab/worker_once.py` (`run_worker_once`) | **CORE — must not delete** | The single-job compute executor; called by the brain itself and by every legacy loop and the dashboard. |
| `scripts/strategy_lab/worker_loop.py` | **keep — off default path** | Standalone 24/7 compute daemon. Not a lifecycle duplicate; the brain bounds its own worker draining per cycle. |
| `scripts/strategy_lab/scanner_farm_loop.py` → `scanner_farm_pipeline.run_cycle` | **ARCHIVE-LEGACY (true duplicate)** | Same watch-intake→sweep-queue arc, flat (no brain/defer/block/classify/validate) — the exact path that produced `already_queued` saturation. **Keep the module** `scanner_farm_pipeline._ensure_local_data` (reused by `farm_coordinator`); retire the *loop* + `scanner_farm_loop.sqlite`. |
| `scripts/strategy_lab/universe_farm_loop.py` → `universe_refill_runner.run_refill_cycle` | **ARCHIVE-LEGACY** | Universe grind to keep the GPU busy — absorbed by the brain's `discovery_refill` pivot. Different mechanism (cursor rotation vs discovery snapshot), same outcome. |
| `scripts/strategy_lab/research_loop.py` / `research_cycle.py` / `research_session.py` | **keep — distinct advisory lane** | LLM-proposal/registry-driven research; the weak model is a JSON-only hypothesis advisor, not the farm controller. It must not queue outside deterministic validation, promote paper/live status, touch configs, or start processes. Archive/merge once the brain absorbs proposal intake. |
| `scripts/strategy_lab/generate_event_sweeps.py` | **keep — off default path** | Price-event sweep generator is unique (no replacement). Its `--from-scanner` lane is the legacy bridge, superseded by the brain's intake. |
| `scripts/strategy_lab/autopilot_once.py` | **keep — off default path** | Cheap deterministic registry→spec→queue filler; superseded by the brain's follow-up logic once stable. |
| `scripts/strategy_lab/run_research_machine_demo.py` | **keep — legacy demo** | The previous end-to-end walkthrough of the bridge path; superseded operationally by `farm_loop`. |
| `scripts/strategy_lab/requeue_stale_jobs.py` | **keep — maintenance** | Manual stale-job recovery (worker_once also auto-reaps). |
| `scripts/strategy_lab/sync_state_db.py` | **keep — must not delete** | Backfill/repair: import completed run dirs when a worker crashed before import. |

## Main engine / legacy strategy logic — what is closed vs what is still open

**The distinction that matters:** the old live/paper *runners* are closed **as trading
engines** — they must not be imported, run, or wired into any order / main / Telegram
path. But their **reusable logic, data, and hypotheses are NOT exhausted** — they can be
extracted into `research_lab` as research hypotheses (lab-native, causal, no live import)
and must pass the farm's normal dry-run → validation path before becoming a candidate.

A baseline port already exists for the headline patterns (so the farm is not empty):

| Pattern | Baseline ported | Still-open hypotheses to extract (research, not engine) |
|---|---|---|
| BB / volume fade | `strategies/bb_fade.py` (`bb_volume_fade`) | `not_thrust` (no ATR-thrust), `slope_fading`, entry-quality filters from the live 5m fade. |
| FVG | `strategies/fvg_family.py` + `features/fvg.py` | lab-native seed only — **not exhausted**; gap-quality, mitigation depth, displacement variants open. |
| Fractal / swing | `strategies/fractal_family.py` + `features/structure.py` | lab-native seed only — **not exhausted**; sweep/retest geometry, multi-pivot structure open. |
| Pump / dump / impulse | `strategies/pump_dump.py` (continuation detector) | **Closed as live engines** (`strategy_pump_reversal_postmortem.md`, `strategy_impulse_postmortem.md` — fee-blocked / forward NO-GO). Still-open as research: event detectors, MFE/MAE logs, pair-risk containment, continuation/geometry hypotheses. |
| Scalp / main `compute_signal` | `strategies/regime_family.py` (`main_fast_swing_regime`, single-TF) | Extract only **isolated hypotheses**: regime labels, lag/freshness, DRIFT both-side behavior, style-specific geometry, late-entry filters. Never import the multi-TF live engine. |

So "ported" means *a baseline seed exists*, **not** "this idea is finished." New
hypotheses from these patterns are welcome — as farm research tasks, never as live imports.

**Hard rule:** the live trading engine (`src/data/*_engine.py`, `main.py`,
`src/exchange/okx_client.py`) is the money path and is **not** imported by the farm —
enforced by `tests/test_farm_loop_integration.py::test_new_modules_have_no_live_trading_coupling`.
Reuse = copy a pure function / re-derive the math in the lab, never import the runner.

## Manual research intake (trader ideas are first-class, not lost)

Trader notes / screenshots / manual calculations are a valid hypothesis source. The
pipeline (deterministic gate before any promotion):

```
trader note / screenshot / manual calc
  -> structured hypothesis card / spec (symbol, timeframe, family/feature, rule, rationale)
  -> dry-run validation (farm_loop --dry-run / a scoped sweep)
  -> farm research task ONLY after deterministic validation
  -> normal lifecycle: classify -> honest validation -> candidate
```

No manual idea is promoted directly to a candidate or to main/live. It enters as a
research hypothesis and earns its place through the same dry-run → validation path as any
other sweep. Tracked in [../BACKLOG.md](../BACKLOG.md) under "Manual research intake".

## Wiring note

`farm_loop` is not yet referenced by any `.bat`/config — it lives on `feature/calc-farm`.
The legacy loops remain the live operator path until that switch is made deliberately
(a follow-up step, not part of this layer).

`farm_loop --run-paper` is now the canonical bridge from validated setup cards to paper
outcomes. The separate `paper_loop` remains useful as a focused diagnostic, but the normal
cycle is farm → validation → setup library → paper inside `farm_loop`.
