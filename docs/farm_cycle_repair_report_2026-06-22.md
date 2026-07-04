# Farm cycle repair + data review — 2026-06-22 (research-only)

A cyclic pass to bring scanner/farm/validator/paper/outcome-memory/dashboard/graph back to a state where
a 10-15h visible cycle runs cleanly. All paper/research-only; no .env / AUTO_TRADE / orders / private
endpoints / Telegram touched. Suite 1622 -> **1633 passed (0 failed)**. Commits 88c41e5, 8cd2ee2.

## 1. What was broken / unfinished after the night
The night cycle ran fully (runs=1334, candidates=14374, farm_results=13796, completed=5012) and was stopped
cleanly, but left technical tails that would degrade the next run:
- **26 tasks stuck in `running`** — masked as active work. The loop is single-process (worker in-process);
  `running` means "materialized into the compute queue", and after a stop nothing is actually running, but
  there was no reconcile, so the 26 looked alive. requeue_stale_jobs.py only touches the legacy compute
  queue, not the farm_tasks brain.
- **true-forward `matured=0` / watched=59** — the decisive lane was DEAD: `true_forward.register()` and
  `collect_once()` existed but were **never called from farm_loop**, so the registry stayed empty and
  nothing ever accumulated forward bars.
- **provider_error=3 / too_short=16 / NEEDS_OI=99** shown as opaque markers with no structural cause.
- **Night human log was UTF-16/null-bytes** (PowerShell Tee-Object), hard to parse.

## 2. What was fixed
- **Stale-running reconcile** — `FarmTasksDB.reconcile_orphan_running()`; called once at apply-loop startup
  (single-process => any boot-time `running` is stale). Applied live: **26 requeued -> running=0**, queue
  14->40. Idempotent, worker dedup skips already-computed. Test added.
- **true-forward wired into the loop** — `register()` (idempotent boundary pin) + bounded `collect_once()`
  now run each apply cycle, crash-isolated. Registry populated: **59 watched, boundaries pinned**. matured=0
  is now HONEST ("no new bars yet" — boundaries just set); it will progress as new bars arrive over the run.
- **tail_diagnostics** (pure, read-only) decodes the tails, surfaced in farm_status_report:
  - provider_error: **GEOD = no_instrument_or_delisted** (absent from live universe; park permanently).
  - too_short: **window_too_short_for_timeframe** for 1d/4h majors (BTC/ETH/AMD/BNB/AAPL 1d) — a fetch-window
    mismatch, NOT fresh listings; the genuine fresh-listing path is separated.
  - NEEDS_OI=99: **oi_unmeasured_no_oi_families_planned** (the loop plans no OI families; park, don't churn).
- **Logging** — the structured `logs/farm/cycle_log.jsonl` is already clean UTF-8 and parseable (verified via
  ConvertFrom-Json); set PowerShell console+output encoding to UTF-8 in the full-cycle bat so the human log
  is UTF-8 too.

## 3. Data accumulated (night)
runs=1334 · candidates=14374 · farm_results=13796 · unique=432 · setups=5012 · paper outcomes=37 ·
validation handoff exported=2488 (PAPER_FORWARD_READY=53). Timeframes 15m=5255/1h=4872/4h=3669.
Data quality ok=5233/thin=6326/no_trades=2237. backend gpu_runs=1256/1256, fallback none.

## 4. Setup classes that appeared (actionable breakdown)
| class | n | dominant family | dominant TF | honest read |
|---|---|---|---|---|
| POSITIVE_VALIDATED | 16 | mean_rev_fade + momentum | 1h/4h | hard-passed PSEUDO-OOS only -> shadow/true-forward |
| PAPER_PLAN_READY | 11 | mixed | — | plan-ready, not edge; awaits forward |
| EXIT_RECOVERED | 44-51 | **all momentum_breakout** | 4h (40/51) | "recovery" = best-variant selection |
| STATISTICAL_CANDIDATE | 14-336 | momentum_breakout | 15m/1h | thin/underpowered |
| THIN_BUT_PROMISING | 184-293 | bb_volume_fade + mean_rev | 1h/15m | tactical lane, forward-watch |
| TACTICAL_1_2_TRADE | 238 | mean_rev_fade | — | one-shot, never statistical edge |
| WRONG_EXIT | 3508 | mixed | — | cluster before any bounded test |

## 5. What was tested of the found setups (honest, from existing gates)
- **EXIT_RECOVERED / exit_recovery**: recovered 29/160 (18%); median baseline net -0.33 -> median **best**
  net still **-0.17** (negative!). Recovery concentrates in a `hold_long` variant = best-variant-per-setup
  SELECTION (overfit), not a real exit edge. exit_phase2: 54 recovered_candidate vs 106 still_bad.
- **shadow OOS (held-out tail)**: 3 evaluated, **0 survived** (noise floor).
- **POSITIVE_VALIDATED / STATISTICAL_CANDIDATE**: concentrated in `momentum_breakout`, the exact family that
  yesterday's rigorous gates (slippage + Šidák deflation + walk-forward, docs/researcher_report_2026-06-21)
  killed, and that mover_validation shows is direction-coin-flip on pseudo-OOS.

## 6. Hypotheses that died (with cause)
- Meme spike-fade (5m/15m): dies under realistic slippage; not significant after deflation; fat loss tail.
- Cross-sectional reversal: negative. Direction prediction from features: unfilterable coin-flip.
- Exit "recovery" as an edge: best-variant selection; median best still negative.
- 1m scalping: cost-bound. TA/candlestick confirmation: no lift. Microstructure pressure: no follow-through.

## 7. Hypotheses that survived but are NOT paper-ready
- **Cross-sectional momentum** (long winners / short losers, 4h, market-neutral): +0.26%/rebalance, win 0.57,
  **t=0.82** — economically sensible, positive tilt, but underpowered. Needs more history, not belief.
- **POSITIVE_VALIDATED (16)** hard-passed pseudo-OOS — sensible candidates, but pseudo-OOS != new bars.

## 8. Ready for shadow/forward (the now-unblocked path)
The 16 POSITIVE_VALIDATED (`hard_passed_await_human_go`) + the `hold_long` momentum/4h EXIT_RECOVERED sliver
are exactly what the true-forward lane is for. Step-1's fix means they will now actually be forward-tested on
GENUINELY NEW bars during the next run (register pinned 59 boundaries; collect runs each cycle). matured !=
edge — a human GO is still required before anything paper, and nothing live ever.

## 9. Still blocked, and why (all honest, bounded — not hangs)
- GEOD 15m/1h/4h: `no_instrument_or_delisted` — park permanently (not on the live universe).
- 16 too_short: `window_too_short_for_timeframe` (1d/4h majors) — deferred with eta; widening the long-TF
  fetch window is the real fix (left as a documented next step, low priority).
- 99 NEEDS_OI: `oi_unmeasured_no_oi_families_planned` — parked; OI lane stays opt-in where 1h/4h OI is dense.

## 10. Commands to run next (the 10-15h cycle)
```
# optional fresh universe first:
python -X utf8 -m scripts.strategy_lab.discover_okx_universe --apply
# window 1 (visible): set caps then
bat\strategy_lab_farm_full_cycle_loop.bat
# window 2: bat\strategy_lab_dashboard.bat   -> http://127.0.0.1:8765
# window 3: build_obsidian_graph + build_graph_viewer loop (10 min)
# window 4: farm_status_report loop (5 min)
# stop: bat\strategy_lab_farm_full_cycle_stop.bat
```
Parseable run log: `<private_root>/logs/farm/cycle_log.jsonl` (clean UTF-8, one JSON object per cycle).

## 11. Checks that passed
- `python -m pytest -q` -> **1633 passed, 0 failed**. New: reconcile_orphan_running + 10 tail_diagnostics.
- `ruff check` clean on all touched files. `git diff --check` clean.
- dry-run sanity (176 tasks planned, validator gating). apply smoke: reconcile ran (running=0), true-forward
  collect ran (registry mtime updated), compute progressed (completed 5000->5012), log parseable, no crash.
- graph/obsidian rebuilt (2239 notes, viewer index.html).

## 12. Commits / pushes
- `88c41e5` fix(farm): reconcile orphan running + wire true-forward lane into the loop.
- `8cd2ee2` feat(farm): decode lifecycle tails into structural reasons + UTF-8 cycle log.
- (this report) docs commit. Branch feature/calc-farm; pushed.

## Bottom line
The contour is now clean for a 10-15h run: no masked stale work, the forward lane is wired and populated,
every tail has a structural reason and next-action, and the log is parseable. The data review confirms the
honest state — the farm's "positive" classes are concentrated in momentum_breakout, which the rigorous gates
already show is a direction coin-flip; the only sensible survivors (16 hard-passed + cross-sectional momentum
tilt) are now routed to true-forward to be decided by GENUINELY NEW bars, not another in-sample sweep.
Nothing is edge or paper-ready.
