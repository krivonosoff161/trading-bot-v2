# Modular trading research/paper loop — 8-cycle build report (2026-06-22, research-only)

Took the paper-signal lane from "built" to a modular, self-correcting research/paper LOOP:
**Search → Signal → Execution Geometry → Paper Forward → Outcome Memory → Visual Review → Operator →
(Main integration boundary, NOT connected to live).** Driven by a Cycle-0 multi-agent audit (6 parallel
auditors + adversarial synthesis), then 7 implementation cycles each audit→implement→run→test→commit.
Hard boundary held: NO orders / .env / AUTO_TRADE / private endpoints / Telegram-creds; old main used as
math/idea reference only (never imported as runtime); LLM advisory-only (cannot mint/alter signals).

## 1. What was (Cycle-0 audit findings)
Paper lane existed (29 signals, 24 reviewed/5 armed) but: replay net was NEGATIVE (-1.90%); dominant
loss `bad_exit_gave_back=10` was diagnosed but UNFIXABLE (tp1/tp2 size_frac decorative, `_close` exited
only at tp_final/stop/timeout); outcome-memory feedback was OPEN-CIRCUIT (`load_known_bad` hardcoded a
dead family name); armed cards stranded (no expiry/no-data age-out, no chart); the lane was NOT wired
into farm_loop; `paper_signals.json` was read only by one CLI; reconcile_orphan_running had no lock.

## 2. What changed (cycles, all committed)
| cycle | change | commit |
|---|---|---|
| 1 Search | `rank_movers`: memory-ranked live-mover universe (buckets + known-bad penalty + good bonus + reasons) + `paper_selection.json` + `--select` CLI | 3d39627 |
| 2 Signal | `FAMILY_META`: 6 families described structurally (class/when/TF/entry/stop/TP/invalidation/required_data/failure_modes) — registry as data | 002a439 |
| 2 Memory-feedback | reconnect `learn_known_bad` to diagnoses review() actually emits (exit-problems/missed-wins no longer mark a setup bad) | 00c0573 |
| 3 Execution Geometry | `exit_mode='partial_be'`: bank 0.5 at tp1 + trail stop to breakeven (the give-back remedy) + known-bad family/symbol-wide fix | 9fa0571 |
| 4 Paper Forward | wire lane into farm_loop (`--run-paper-signals`, bounded/crash-isolated) + `age_out` stale-armed expiry (past expires_at / repeated no_data) | dfe1882 |
| 6+7 Visual+Operator | armed cards get a chart + `chart_context_ref`; single-process loop lock-file guard | ccca40a |

## 3. Cycles actually run (bounded, fresh OKX)
- Cycle-0 audit workflow: 7 agents, 543k tokens, 79 tool-uses.
- A/B exit comparison (59-signal replay tail). Live apply cycle (5 armed + charts + selection snapshot).
  `farm_loop --once --apply --run-paper-signals` (lane advanced inside the farm cycle, armed 5→10).

## 4. Signals generated / armed / terminal
Live apply: 5 armed (all with chart_context_ref). farm-cycle smoke: 10 armed. (Earlier replay session: 24
terminal across 5 families.) Live-armed forward cards mature on wall-clock bars over 24-48h.

## 5. Outcomes: R / net% / diagnosis (A/B proof of the main lever)
Same 59-signal replay tail, baseline `fixed` vs new `partial_be`:
- fixed: sum_net% **+74.65**, avg +1.78%, `bad_exit_gave_back=6`.
- partial_be: sum_net% **+79.50**, avg +1.89%, `bad_exit_gave_back=5`, **+2 partial_breakeven_save**.
The partial+breakeven converts give-backs into small wins WITHOUT look-ahead (bar j uses only bars ≤ j).
Modest but real and correctly targeted.

## 6. Families working / not
good_signal concentrated in reversal_fade + early_tp_tactical + sweep/pullback (the 5-family expansion
produces winners where the original continuation-only lane stopped out). early_tp_tactical is
double-edged (good_signal but also stop_too_tight). watch_only correctly suppresses choppy no-trade tape.

## 7. What memory changed in the next cycle
`learn_known_bad` (≥3 genuine-failure outcomes → skip a (symbol,tf,family)); `family_priority` (orders
families by good-rate); `rank_movers` (penalises symbol-wide confirmed-bad, rewards prior good). Tests
prove a learned-bad setup is NOT regenerated and that priority reorders deterministically.

## 8. Visual artifacts
Every terminal AND every armed card now writes `paper_reviews/<id>.md` (ASCII path + levels + diagnosis)
+ `.png` (price + entry zone + stop + TP). Surfaced in farm_status_report ("paper signals: by_status").

## 9. Operator commands
```
python -X utf8 -m scripts.strategy_lab.paper_signals_run --select          # memory-ranked universe + reasons
  ... --mode live                                                          # dry-run (writes nothing)
  ... --mode live --apply                                                  # seat armed cards + charts + snapshot
  ... --mode live --loop 8 --sleep-seconds 600 --stop-file <root>\state\STOP_PAPER.txt
  ... --mode replay --loop 4                                               # diagnostic to seed memory
  ... --status                                                             # cards + diagnosis counts
  ... --apply --notify                                                     # Telegram (only if token+chat in env)
python -X utf8 -m scripts.strategy_lab.farm_loop --loop --apply --run-worker --run-validation \
    --run-paper --run-paper-signals --enrich-funding --enrich-oi --stop-file <root>\state\STOP_FARM_FULL_CYCLE.txt
python -X utf8 -m scripts.strategy_lab.farm_status_report                  # operator picture (incl paper signals)
```
n8n/operator-ready: each module is a CLI with --dry-run/--apply/--once/--loop/--stop-file and a JSON
status file (paper_signals_status.json / paper_selection.json / paper_signals.json). Compute, notification,
visualization, and decision stay SEPARATE; dashboard/graph/Telegram are surfaces, never decision makers.

## 10. Tests passed
37 paper-signal targeted tests (contract gate, store, search ranking, families+metadata, lifecycle,
partial-breakeven exit, age-out, learning/no-regeneration, richer diagnosis, AST no-live-order) + farm_loop
integration. ruff clean, git diff --check clean. Full suite: see final commit.

## 11. Commits / push
3d39627, 002a439, 00c0573, 9fa0571, dfe1882, ccca40a (+ this doc). Branch feature/calc-farm.

## 12. What cannot be claimed publicly
NO edge, NO "profitable", NO "paper-ready/ready to trade". The replay net being positive on one fresh tail
is NOT forward proof (held-out/replay ≠ true-forward); armed cards have not matured. Standing verdict
stands: search space is sub-cost; the only open avenues are forward + better exits — both now built, not yet
proven.

## 13. Blocked / deferred (honest)
- Forward EVIDENCE: live-armed cards need 24-48h wall-clock bars to mature (seated, pending) — the
  `--run-paper-signals` farm wiring is what will mature them on an overnight run.
- Dedicated HTML dashboard panel + graph/obsidian nodes for paper signals (farm_status CLI surfaces them
  now; the HTML/graph reader is the one remaining surface follow-up).
- Long-TF (1d/4h) data-window widening (16 too_short majors); NEEDS_OI=99 parked (opt-in OI families);
  GEOD delisted (park). Farm-coordinator plan-time known-bad gate (the paper lane gates at selection; the
  farm core gate is a larger, higher-risk change deferred to keep the 1600+ suite green).
