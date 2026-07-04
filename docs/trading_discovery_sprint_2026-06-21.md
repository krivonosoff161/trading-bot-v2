# Trading-discovery + implementation sprint — result (2026-06-21)

Not "I built a module". A working search machine that looks where the market moves, fixes exits, judges
tactical setups on their own axis, revives archived ideas, and keeps a real knowledge base. All
research-only; no live/orders/AUTO_TRADE/.env/private/Telegram touched.

## 1. What was broken in the setup
Stale alphabetical universe blind to live 20-60% movers; tokenized equities polluting the crypto
universe; 3 of 25 families running; tactical 1-9-trade setups un-judged (mislabeled FAILED_OVERFIT);
wrong-exit the #1 wall but never the focus; OI families data-starved; 1425 n<3 "rejects" that were
actually non-events.

## 2. What was fixed in code (committed)
| block | module | commit |
|---|---|---|
| 1 live universe | `live_universe_selector.py` — movement-ranked intake (priority 1) over the grind; equities split | 0f73bfc |
| 2 exit-first | `exit_first_resim.py` — exit grid over the WHOLE wrong-exit pool, all families | 4cc260a |
| 3 SFP | `strategies/sfp.py` + registry — liquidity-sweep reversal family | 16c7722 |
| 4 tactical track | `tactical_track.py` — parallel verdict lane, NO_EVENT/TACTICAL_LEAD/… never paper-ready | 193271c |
| 6 OI backfill | ran `flow_enrich.enrich_oi_one` (keyless rubik) — 1h/4h coverage 12%→22% | (data) |

## 3. Data recomputed
- Live universe snapshot (387 tickers → 30 ranked movers, equities separated).
- 34 live_mover intake events registered at priority 1 (consumed before the grind).
- OI backfilled onto 20 more 1h/4h files (coverage 12%→22%).
- Candles fetched + saved for 6 live movers (BICO/RESOLV/MET/AERO/UB/SOON) — farm can now compute them.

## 4. Live movers now in the farm
fresh_movers queued first: BICO(+17%/range65%), RESOLV(+20%), LAB(+34%), O(-30%), UB(+20%), MET(+19%),
RE, MMT. The farm is no longer blind to the 20-60% moves.

## 5. Exit candidates found
Exit-first re-sim: pool 156, evaluated 63, **13 exit_recovered_candidate in-sample** (hold_long 8,
early_tp 4). Top: BILL/15m MRF early_tp +2.69, BREV/4h momentum hold_long +2.03, BICO/1h bb +0.91. None
bridge-passed (needs_forward_only 0) → in-sample only.

## 6. Tactical leads found
**77 TACTICAL_LEAD** (thin positive + good capture): ASTER/4h net+7.18 cap0.63, ALGO/1h net+4.26 cap0.91,
BOME/4h net+5.04 cap0.68, BASED/15m net+4.24. Plus 215 UNDERPOWERED_POSITIVE. 293 forward-watch total.
All previously invisible (the validator threw them away as FAILED_OVERFIT).

## 7. Archived ideas revived
SFP/liquidity-sweep — added as a family (was the one survivor idea with no implementation). Registered
families now 26. Stat-arb Kalman flagged as a separate research lane (owner GO, non-blocking).

## 8. Families actually run
The 3 baselines + SFP on the live-mover bounded cycle. **Headline:** momentum_breakout on 4h live movers
posts POSITIVE median net (MET +5.37%, AERO +1.99%, RESOLV +1.72%) vs −0.25% on the stale universe —
strategy-universe fit, not a missing edge.

## 9. Promising (research-only, NOT edge)
- momentum_breakout on 4h movers (positive in-sample, needs OOS/forward).
- 77 tactical leads (forward-watch).
- 13 exit-recovered candidates (in-sample).
- SFP on movers (mixed; SOON/MET 1h positive) — needs the live-mover sweep + exit-first.

## 10. Confirmed-bad
momentum_breakout on the stale dead universe (median −0.25); tape/orderbook microstructure pressure
(proven null); maker-execution rescue (Gate-1 mirage).

## 11. No-event (not bad)
1244 candidates n<3 — never triggered. Now labeled NO_EVENT, not a failure.

## 12. Forward-watch
293 (77 leads + 215 underpowered-positive + 1 CBRS). Plus the true_forward collector for new bars.

## 13. Continuous-loop ready
Live universe → priority intake → farm → exit/tactical/validator split → memory → status. Run the live
selector on a cadence to keep the universe fresh; the farm consumes movers first.

## 14. What must NOT be promoted
Everything above is research-only. No tactical lead / exit-recovered / mover-momentum result is
paper-ready. The statistical validator (paper-ready gate) was NOT loosened — the tactical track is a
parallel lane. `paper_ready_leak = 0` invariant holds.

## 15. Owner run commands
```
# refresh the live universe and queue movers first (keyless public):
python -m src.research_lab.live_universe_selector --apply --snapshot
# exit-first re-sim over the wrong-exit pool:
python -m src.research_lab.exit_first_resim --snapshot
# tactical track (leads / no-event / known-bad):
python -m src.research_lab.tactical_track --snapshot
# OI backfill (bounded keyless), then OI families:
python -m src.research_lab.oi_family_research --snapshot
# status (now shows tactical track + live universe):
python scripts/strategy_lab/farm_status_report.py
```

## Validator note (Block 5)
The gates are correct for statistical edge and were NOT loosened. The tactical track provides the
honest parallel labels (UNDERPOWERED/NO_EVENT/LEAD) WITHOUT touching the paper-ready gate. DSR exists in
the vendor lib but is unused; wiring it in place of the Šidák-on-perm-p is a separate careful pass (it
would only tighten, not loosen) — left as a flagged next task, not done blind.
