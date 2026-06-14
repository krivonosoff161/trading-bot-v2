# Strategy Lab Remaining Work

Date: 2026-06-14. Updated after overnight-readiness patch.

This document tracks what is still required before the Strategy Lab can be treated
as a low-surprise overnight research machine.

## Overnight Readiness: YES (no-LLM)

After this patch, the Strategy Lab is safe for **overnight no-LLM runs**:

- One command starts a bounded research loop (`bat\strategy_lab_research_loop_overnight_no_llm.bat`).
- Default duration is 480 minutes; set `STRATEGY_LAB_LOOP_MINUTES` for shorter day runs.
- Morning report summarizes results (`bat\strategy_lab_morning_report.bat`).
- Graceful stop writes intent; current iteration finishes cleanly.
- Stale running jobs can be requeued explicitly.
- Timeframe contract prevents silent wrong-timeframe execution.
- Multi-timeframe data (15m/1h/4h/1d) can be prepared via the OKX public provider.
- All safety guards intact: no live trading, no order engine, no paid LLM by default.

## What Changed (this patch)

### P0 — Timeframe contract (Phase 1)
- `ExperimentSpec` now has an explicit `timeframe` field (default "1d").
- `choose_symbol_file()` is timeframe-aware: only picks files matching the requested TF.
- `compile_proposal()`, `compile_sweep()`, and `research_plan` pass timeframe through.
- Worker returns empty results when no matching file exists (no silent wrong-TF execution).
- 8 new tests covering TF contract edge cases.

### P0 — Multi-timeframe loader (Phase 2)
- OKX public provider now supports 1m, 15m, 1h, 4h, 1d (was 1m only).
- Synthetic provider supports all 5 timeframes for testing.
- `prepare_market_data()` writes candles per timeframe under `market_data/{tf}/`.
- `paths.py` adds `market_data_dir()` and `market_data_glob()` for any TF.
- 12 new tests covering multi-TF provider, readiness, and prepare workflow.

### P0 — Overnight operator bat (Phase 3)
- `bat\strategy_lab_research_loop_overnight_no_llm.bat` — one-command safe overnight run.
- Prints private root, duration, sleep, queue cap, worker cap, LLM state, morning command.
- Operator guide updated with overnight section.

### P0/P1 — Graceful stop + stale requeue (Phase 4)
- `stop_intent.py` — write/check/clear stop-intent file under private root.
- Research loop checks stop intent between iterations.
- `requeue_stale_jobs.py` — dry-run shows stale jobs; --apply requeues them.
- `bat\strategy_lab_graceful_stop.bat` and `bat\strategy_lab_clear_stop.bat`.
- 7 new tests covering stop intent, stale detection, and requeue behavior.

### P1 — Morning report (Phase 5)
- `morning_report.py` — summarizes last loop: jobs, strategies, symbols, candidates,
  rejects, LLM cost, data missing by timeframe, stale hints, next command.
- `bat\strategy_lab_morning_report.bat`.
- 6 new tests covering empty state, loop data, stop reflection, no absolute paths.

### P1 — LLM tiny test harness (Phase 6)
- `bat\strategy_lab_llm_tiny_test.bat` — refuses unless env is set; tiny caps;
  cost warning; no live trading.
- 8 tests verifying env gates and safety.

### P1 — Proposal quality scoring (Phase 7)
- `rejection_reason_counts()` tallies why proposals were rejected.
- Morning report shows proposal rejection reasons by category.
- 6 tests covering all major rejection reasons.

## P1 Still Remaining

1. **Tiny real LLM live test (manual step)**
   - Run `bat\strategy_lab_llm_tiny_test.bat` with real provider env.
   - Verify JSON contract, validation, usage accounting, contract breaker.
   - Do not use overnight LLM until this passes.

## P2 Later Work

2. **GPU backend** — keep future-only until CPU path is stable.
3. **Richer dashboard** — queue/history charts, per-TF data coverage, candidate drilldown.
4. **More strategies and parameter families** — add only after execution/data contract is reliable.

## Safe Commands Today

No-LLM overnight:

```powershell
cd C:\Users\krivo\trading-bot-v2
.\bat\strategy_lab_research_loop_overnight_no_llm.bat
```

5-7 hour day run:

```powershell
cd C:\Users\krivo\trading-bot-v2
$env:STRATEGY_LAB_LOOP_MINUTES = "360"
.\bat\strategy_lab_research_loop_overnight_no_llm.bat
```

Use `300` for 5 hours, `360` for 6 hours, or `420` for 7 hours.

Morning report:

```powershell
.\bat\strategy_lab_morning_report.bat
```

Status:

```powershell
python -m scripts.strategy_lab.status
```

Prepare multi-TF data:

```powershell
python -m scripts.strategy_lab.prepare_1m_data --dry-run
```

Graceful stop:

```powershell
.\bat\strategy_lab_graceful_stop.bat
```

Requeue stale jobs:

```powershell
python -m scripts.strategy_lab.requeue_stale_jobs --dry-run
python -m scripts.strategy_lab.requeue_stale_jobs --apply
```

Do not use paid LLM overnight until the tiny live test passes.
