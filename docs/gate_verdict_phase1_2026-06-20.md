# Gate Verdict — Phase 0+1, bounded farm gate-run (2026-06-20)

Public pointer. Full numbers + per-family breakdown live in the private research repo:
`scripts/analysis/research/gate_verdict_phase1_2026-06-20.md`.

**Verdict: (B) — the current search space is sub-cost / fragile. No new validated edge.**

After Phase 0 (hardening: reproducible fail-loud validation, off-by-default visibility,
discovery freshness, dashboard observability, honest OI gate, recursive money-path guard,
legacy-loop abort) and Phase 1 (search quality: real per-family stop/take/hold grids with
bounded tiers, multiple-testing correction, revived REGIME_SWEEP follow-ups, GPU benchmark),
a bounded full-loop gate-run on real public OKX data (paper-only) produced:

- 84 fresh hard validations → **0 `PAPER_FORWARD_READY`**;
- dominant failure `FAILED_COSTS` (76–83%): gross edge eaten by fees+slippage;
- the only `PAPER_FORWARD_READY` cards are pre-Phase-1 leftovers with thin, inconclusive
  paper outcomes (net ≈ noise, time-exits) that did not pass the new multiple-testing bar.

The farm machine works end-to-end and is honest (full suite green, no-lookahead, GPU/CPU
parity, real OI, fail-loud validation). The result is a real research finding: there is no
net-positive directional edge in the current universe × 15m–4h × taker-cost space.

**Decision:** P2 (Stage 6b full rejected-mining, Stage 8 true forward paper) stays closed.
The next move is a constraint change (deeper/longer data, maker execution, a different
regime/instrument class, or a thin market-neutral return) — to be decided with the trader,
not more pipeline breadth.

Reproduce: `python -m scripts.strategy_lab.farm_status_report` (see the validation handoff
`hard_status` line) and a fresh-report tally over `hard_validation/reports`.
