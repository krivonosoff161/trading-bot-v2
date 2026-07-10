# Strategy E Postmortem

Status: **LEGACY EXPLORATORY EVIDENCE**. Closed in March 2026.

## Hypothesis

The idea combined a higher-timeframe trend filter, an intermediate pullback,
and a lower-timeframe breakout. It was evaluated in a demo/paper context using
an early polling-based market-data path.

## Outcome

The combined conditions produced too few usable candidates. Relaxing individual
thresholds moved the blockage between filters rather than producing a stable
sample. The hypothesis was closed instead of being tuned indefinitely.

## What It Taught

- Individually sensible filters can become mutually restrictive when combined.
- A visual market concept needs a measurable definition before implementation.
- A sequence of empty or structurally blocked runs is evidence about the
  hypothesis, not a reason to keep loosening parameters.
- Research should establish a measurable candidate pattern before production
  code is built around it.

## Current Relevance

This is not an active family and is not part of the supported paper path. The
lesson remains relevant to farm work: blocker reasons and candidate frequency
must be recorded, not hidden behind a binary pass/fail outcome.
