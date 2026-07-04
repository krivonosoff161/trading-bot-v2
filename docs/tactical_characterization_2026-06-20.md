# Tactical / Rejected-Setup Characterization (T1+T2) — 2026-06-20

Public pointer. Full tables + per-family/timeframe numbers in the private research repo:
`scripts/analysis/research/tactical_characterization_2026-06-20.md`.

Read-only, derive-only: no compute, no re-sweep, no DB migration, no trading/validation/
sweep/paper logic change, no money path. Reproduce with
`python -m scripts.strategy_lab.farm_status_report` (COMPLETION / reconcile / tactical shelf
lines) and `src.research_lab.trade_path_diagnostics`.

## Result

A rejected setup is not proven-bad. Of **3475 rejected/failed candidates, 1623 (47%) are
recyclable** (a structural reason the hard validator could not see), and the largest class is
**wrong_exit (1054)**: the move happened (median MFE +1.55%) but the exit gave it back
(median net −0.30%, capture −2.55) — an EXIT problem, not a signal problem.

The hard validator structurally cannot score thin setups (n<3 NEEDS_MORE_DATA, n<10 splits,
n<6 PSR), so 1–2 trade windows are mechanically rejected regardless of the window. Tactical
labels are research-only and NEVER equal PAPER_FORWARD_READY / grant trade access (invariant
test).

## Recoverable (T3, needs GO — spends compute)

- wrong_exit (1054) → exit-grid / trailing re-sweep (momentum_breakout first);
- validator_too_strict (85 net-positive, 3–9 trades) → re-validate under a tactical bar;
- tactical_candidate (100 net-positive, 1–2 trades) → SHADOW forward paper only;
- NEEDS_OI_CONTEXT → enable OI families on 1h/4h (15m stays oi_unmeasured; no fake-pass).

## Not recoverable (53%, leave alone)

confirmed_bad 603 (n≥10, net-negative — validator had power), insufficient_data 688 (0 trades),
uncharacterized 561.

## Queue/OI honesty

COMPLETION = PAUSED_WITH_WORK (18 eligible, loop stopped ≠ drained); NEEDS_OI_DATA=99 is an
UNOWNED stale-old-path artifact (OI families aren't planned by the current loop) →
`oi_unmeasured` derived; UNKNOWN=84 was a cosmetic empty-status → LEGACY_UNSCORED; the
ready-for-validation backlog (233 groups / 425 unvalidated) was masked by a LIMIT 40 preview.
