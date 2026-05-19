# Research Damage Report - 2026-05-19

## Scope

This report corrects the methodological error in `docs/gpt_full_research_18_05_2026.md`.

The old report blended two different populations:

- archive REST scanner: `170` records, majors-only (`BTC/ETH/SOL/XRP/DOGE`);
- live WS main screener: `82/85` records depending on label cutoff, 29-pair alt-heavy universe.

Those populations must not be combined for live production win-rate, profit-factor, or average-R decisions.

## Invalid Or Non-Production-Grade Sections

### Executive Summary

Invalid for production decisions:

- "`FAST x DRIFT` is healthy and production-worthy on unified data: `n=71`, `WR=80.3%`, `avg_R=+0.09R`."
- "`SWING x TRENDING` is the main problem bucket on unified data: `n=54`, `WR=68.5%`, `avg_R=-0.10R`."

Why invalid: both are blended archive/live metrics. They describe a mixture, not current WS behavior.

### Data Notes

Invalid framing:

- "Block 1 unified dataset"
- `archive_scanner=170` plus `live_main=82`

Why invalid: the data note correctly states the blend, but the downstream report treats the blend as decision-grade.

### Block 1 - Unified Regime x Style Matrix

Invalid for production decisions:

- every `WR`, `avg_R`, `PF`, and `solid/preliminary` verdict in the unified matrix.

Use `docs/ws_truth_report.md` instead for current WS metrics.

### Worst Pairs Inside TRENDING x SWING

Partially valid only as a descriptive live-alt observation.

Invalid use: deciding global style changes from a table that is embedded in a blended Block 1 section.

### Live vs Archive Bias

Directionally useful, but not sufficient.

Valid idea: live and archive diverge materially.

Invalid follow-through: continuing to present unified metrics after identifying that divergence.

### Pump Block

Risk containment findings remain useful, but the framing was too weak.

Invalid framing: continuing threshold-level tuning after the B.5 backtest and live results both showed a roughly 38% WR ceiling.

Correct framing: current pump architecture has no demonstrated edge; further tuning is not justified without Phase C redesign.

### BB Fade Block

Still preliminary.

Archive pattern mining must not be treated as live WS truth. Any BB Fade production decision needs live-only labels.

## What Remains Valid

- No production code under `scripts/ws/*.py` or `config.yaml` was modified by the research scripts.
- `docs/gpt_majors_vs_alts_19_05_2026.md` remains useful as archive replay / filter attribution, not as live WR truth.
- The `TRENDING x SWING` archive-majors replay finding remains hypothesis-grade:
  - `min_vol_ratio_trending` and `slope/adx_rising` are independent cuts;
  - overlap between those two cut groups was `0`.
- Pump risk containment remains valid:
  - `session_ban_sl_no_tp=2`;
  - quarantine / pair-risk overrides for `APR/RIVER/LAB`.
- The no-tape finding for `APR/RIVER/LAB` remains valid for current local tape availability.

## Decisions That Must Not Be Made From The Old Report

- Do not treat `WR=80.3% FAST x DRIFT` as live WS truth.
- Do not deploy main-scanner changes from the unified Block 1 matrix.
- Do not kill or promote `SWING` globally from the blended `TRENDING x SWING` result.
- Do not continue pump parameter tuning based on Sim tables alone.
- Do not use context-based pattern claims without acknowledging snapshot coverage:
  - `85` labeled WS signals;
  - `59` matching `ws_main_screener` snapshots;
  - `26` missing context rows;
  - `2` extra snapshots are `source=ws_scanner` and do not join to `main_signals_labels.jsonl`.

## Replacement Artifacts

Use these instead:

- `docs/snapshot_coverage_audit.md`
- `docs/ws_truth_report.md`
- `docs/ws_pattern_mining_report.md`
- `docs/pump_architecture_verdict_19_05_2026.md`

Archive data may still be used for replay and hypothesis testing. It must not be blended into live WS production metrics.
