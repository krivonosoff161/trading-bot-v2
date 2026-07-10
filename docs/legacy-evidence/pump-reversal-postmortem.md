# Pump Reversal Postmortem

Status: **LEGACY EXPLORATORY EVIDENCE**. Closed in May 2026.

## Hypothesis

The experiment looked for a reversal after a rapid low-timeframe price move in
volatile instruments. It tested event detection, short holding windows, and
cost-aware exits in paper/research conditions.

## Outcome

The apparent gross reversal behavior did not establish a positive net result
once fees, liquidity-taking assumptions, and realistic exit handling were
included. Parameter variation did not remove that structural limitation, so
the hypothesis was closed.

## What It Taught

- Win rate alone is not an economic result.
- For short-horizon work, fees and execution assumptions can dominate the
  observed move.
- A small or thinly sampled apparent advantage is not a basis for a general
  rule.
- Negative results should be preserved as a boundary: the current workbench
  must model costs before a candidate is treated as useful evidence.

## Current Relevance

This is not an active strategy. The reusable output was methodology only:
event provenance, cost-aware measurement, and the requirement to distinguish
gross observations from net outcomes.
