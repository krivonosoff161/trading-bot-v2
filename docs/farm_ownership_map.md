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
| `scripts/strategy_lab/research_loop.py` / `research_cycle.py` / `research_session.py` | **keep — distinct lane** | LLM-proposal/registry-driven research; an axis the brain does **not** yet cover. Currently the operator default (overnight `.bat` files). Archive once the brain absorbs proposal intake. |
| `scripts/strategy_lab/generate_event_sweeps.py` | **keep — off default path** | Price-event sweep generator is unique (no replacement). Its `--from-scanner` lane is the legacy bridge, superseded by the brain's intake. |
| `scripts/strategy_lab/autopilot_once.py` | **keep — off default path** | Cheap deterministic registry→spec→queue filler; superseded by the brain's follow-up logic once stable. |
| `scripts/strategy_lab/run_research_machine_demo.py` | **keep — legacy demo** | The previous end-to-end walkthrough of the bridge path; superseded operationally by `farm_loop`. |
| `scripts/strategy_lab/requeue_stale_jobs.py` | **keep — maintenance** | Manual stale-job recovery (worker_once also auto-reaps). |
| `scripts/strategy_lab/sync_state_db.py` | **keep — must not delete** | Backfill/repair: import completed run dirs when a worker crashed before import. |

## Main engine / legacy strategy logic — extraction decision

**No net-new extraction is warranted.** Every legacy strategy pattern is already a
research_lab family with its own causal, lab-native feature implementation (the lab
imports nothing from `src/strategy`):

| Pattern | Status |
|---|---|
| BB / volume fade | ported — `strategies/bb_fade.py` (`bb_volume_fade`). Live 5m extras (ATR-thrust/slope-turn) are an *exit-disease* refinement, not warranted before the base family clears validation. |
| FVG | ported — `strategies/fvg_family.py` + `features/fvg.py`. |
| Fractal / swing | ported — `strategies/fractal_family.py` + `features/structure.py`. |
| Pump / dump | ported (continuation detector) — `strategies/pump_dump.py`. **Reversal + impulse variants are CLOSED** (`strategy_pump_reversal_postmortem.md`, `strategy_impulse_postmortem.md`: "Не реанимировать без нового research"). Do not revive. |
| Scalp / regime | ported single-TF — `strategies/regime_family.py` (`main_fast_swing_regime`). The multi-TF live `compute_signal` is the *product*, deliberately not a lab family (Strategy E postmortem: multi-filter confluence → 0 signals). |

The live trading engine (`src/data/*_engine.py`, `main.py`, `src/exchange/okx_client.py`)
is the money path and is **not** imported by the farm — enforced by
`tests/test_farm_loop_integration.py::test_new_modules_have_no_live_trading_coupling`.

## Wiring note

`farm_loop` is not yet referenced by any `.bat`/config — it lives on `feature/calc-farm`.
The legacy loops remain the live operator path until that switch is made deliberately
(a follow-up step, not part of this layer).
