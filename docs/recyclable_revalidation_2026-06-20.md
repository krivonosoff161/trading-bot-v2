# Phase D — honest re-validation of the recyclable shelf

**Goal.** Run the recyclable rejects — exit_recovered (wrong_exit a different in-sample exit rescued),
validator_too_strict (3–9 trades, net-positive, below n=10 power), tactical_candidate (1–2 trades) —
through the SAME honest-backtest bridge the validator uses, WITH the multiple-testing (Šidák)
correction, to answer the only honest question: **does any survive proper statistics?**

[recyclable_revalidation.py](../src/research_lab/recyclable_revalidation.py) re-simulates each
candidate (exit_recovered under its recovered exit, `n_trials` = exit-grid size so the best-of-grid
choice is deflated; others at baseline, `n_trials`=1), builds a `CandidateForValidation`, and runs
`run_validation(dry_run=True)` — **no verdict artifact is written**, so the canonical lifecycle and
`hard_status` are untouched. Research-only, read-only re-sim, no money/order/live path.

## Result (720 re-validated)

| bucket | n | verdict breakdown |
|---|---|---|
| validator_too_strict | 293 | **FAILED_OOS 293** (all — n<10, genuinely underpowered) |
| tactical_candidate | 267 | **NEEDS_MORE_DATA 267** (all — n<3, can't be scored) |
| exit_recovered | 160 | FAILED_OOS 103 · REGIME_ONLY 54 · FAILED_FRAGILITY 2 · **PAPER_FORWARD_READY 1** |

**719 / 720 fail honest re-validation.** The validator was **not** "too strict" — the 3–9-trade and
1–2-trade buckets are correctly underpowered. The recyclable shelf is **characterization, not edge**,
confirming the corpus gate verdict (sub-cost).

## The one survivor

`CBRS_USDT_SWAP / 4h / mean_reversion_fade / hold_long exit / n=13` cleared every check including the
Šidák deflation (CI>0 AND adj-p<0.05 over 8 grid trials). Consistent with T3-A (mean_reversion_fade
recovers via a better exit). **But it is one in-sample survivor out of 720 tests** — at α=0.05 after
deflation, ~1 false positive is exactly what you would expect by chance, so this is at the noise
floor, **not proven edge**.

**It is NOT promoted.** In the Setup Outcome Memory it carries `revalidation_status =
PAPER_FORWARD_READY` but keeps `paper_forward_ready = False` and its canonical `outcome_class`
(`WRONG_EXIT`); the invariant `paper_ready_without_hard_pass` stays 0. Promotion needs a **human GO +
genuine OOS forward-paper** — never automatic.

## What goes into memory

All 720 verdicts (positive AND negative) are recorded as a research-only `revalidation_status` per
`uc_key` in `state/derived/recyclable_revalidation.json`, attached by `build_memory_index`. The
negatives are the point: the farm now KNOWS these were re-tested and failed, so it won't re-chase
them. Reproduce: `python -m src.research_lab.recyclable_revalidation --snapshot`. Live numbers:
`scripts/analysis/research/recyclable_revalidation_2026-06-20.md`.
