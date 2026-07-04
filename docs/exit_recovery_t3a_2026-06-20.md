# T3-A — Exit-Recovery for wrong_exit rejects — 2026-06-20

Public pointer. Full tables + cost sensitivity + examples in the private research repo:
`scripts/analysis/research/exit_recovery_t3a_2026-06-20.md`.

**Verdict: wrong_exit is mechanically real (a different exit systematically improves net on
the same signals), strongly family-dependent, but recovered ≠ proven edge.**

Read-only bounded re-simulation (`src/research_lab/exit_recovery.py`): regenerate the same
entry signals on the same local candles, re-simulate a per-family fixed-barrier exit grid
(earlier-TP / asymmetric RR / timeout) vs the re-simulated baseline. No look-ahead, no farm
loop / validator / paper / money path touched. Trailing/break-even deferred to phase 2.
Reproduce: `python -m src.research_lab.exit_recovery --plan` / `--family ... --limit N`.

## Result (160 candidates: momentum 80 / bb_fade 50 / mean_rev 30)

- **mean_reversion_fade: 10/30 recovered (33%), median best +0.67%** — earlier take-profit.
- bb_volume_fade: 9/50 recovered (18%), median best +0.08%.
- momentum_breakout: 14/80 (17.5%), median best still −0.14% — exit does NOT rescue it.
- Overall: **33/160 recovered (n≥5)**; 17 first-pass "recoveries" were 1–4 trade noise (removed).
- Recovered survive 20 bps round-trip cost (not on the knife-edge) — but in-sample.

## Honesty

Recovered requires net > cost, beats baseline, AND n_trades ≥ 5. The best-of-grid exit is
**in-sample** — recovered means "worth honest re-validation with multiple-testing", NOT
"edge proven". The 33 form a research-only `exit_recovered_candidate` class
(`paper_forward_ready=False`, invariant test), never a trade signal.

## Next (needs GO — compute)

Re-validate the 33 under their recovered exit through the honest bridge WITH multiple-testing;
prioritize mean_reversion_fade; treat momentum_breakout wrong_exit as near-confirmed (exit
doesn't help); phase-2 = add trailing/break-even exit mode.
