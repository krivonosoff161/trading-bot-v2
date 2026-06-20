# Trade-path instrumentation + bounded backfill (Phase B)

**Goal.** Record the *path* of a trade, not just its final net, so the Setup Outcome Memory can
explain *why* a setup was rejected: time-to-MFE / time-to-MAE, TP-before-SL ordering, bars-to-TP/SL,
and a one-word `path_quality`. Instrumentation only — the exit/validation logic is unchanged.

## What changed (additive, no look-ahead, no exit-logic change)

[experiment.py](../src/research_lab/experiment.py) `finalize_trade` now emits, per trade, over the
**already-decided** hold window `[entry_idx, exit_idx]` (so it adds no look-ahead and never changes
the exit decision):

| field | meaning |
|---|---|
| `time_to_mfe` / `time_to_mae` | bar offset from entry at which max favorable / adverse excursion occurred |
| `tp_before_sl` | `True` if the take fired first, `False` if the stop did, `None` on timeout |
| `bars_to_tp` / `bars_to_sl` | bars to the realized TP / SL (else `None`) |
| `bars_held` | realized hold length |
| `adverse_before_favorable` | the drawdown came before the run-up (entry took heat first) |
| `path_quality` | `no_move` / `clean_capture` (capture≥0.7) / `gave_back` (capture<0.3, the wrong_exit signature) / `heat_first` / `partial` |

Because `finalize_trade` is the single trade-arithmetic seam shared by the CPU simulator, the GPU
simulator, and the paper runtime, all three emit these fields identically by construction.

## Bounded backfill

[trade_path_backfill.py](../src/research_lab/trade_path_backfill.py) regenerates the SAME entry
signals on the SAME local candles for the recyclable rejects and re-runs `simulate_trades` (which now
emits the path fields), aggregates the per-trade path into one record per candidate, and writes the
derived `state/derived/trade_path_backfill.json`. The Setup Outcome Memory attaches these per-`uc_key`
(`time_to_mfe`, `time_to_mae`, `tp_before_sl_share`, `adverse_first_rate`, `path_quality`).

Read-only re-simulation: same params, same fixed-barrier exits, no promotion, no money/order/live path,
bounded by `--limit`. Reproduce: `python -m src.research_lab.trade_path_backfill --plan` /
`--limit N --snapshot`.

## Live finding (1623 recyclable rejects, 0 skipped)

`path_quality` over ~64k re-simulated trades: **gave_back 62%**, clean_capture 16%, heat_first 14%.

| sub-reason | n | avg capture | avg time-to-MFE (bars) | tp-before-sl share |
|---|---|---|---|---|
| **wrong_exit** | 1054 | **−3.79** | **1.19** | **0.9%** |
| validator_too_strict | 293 | −0.32 | 1.72 | 0.3% |
| tactical_candidate | 267 | +0.55 | 2.05 | 0.6% |
| wrong_timeframe | 9 | +0.53 | 1.91 | 0.0% |

**Mechanism, now visible at the path level:** for `wrong_exit` the favorable move arrives almost
immediately (~1.2 bars) and is then surrendered — the take-profit almost never fires before the stop
(0.9%). That is an exit-geometry problem (a far TP that the fast, mean-reverting move never reaches),
not a signal problem — consistent with T3-A (earlier-TP recovers mean-reversion). It is **not** an
edge: `gave_back` dominating and the corpus gate verdict (sub-cost) both still stand. Full numbers:
`scripts/analysis/research/trade_path_backfill_2026-06-20.md`.
