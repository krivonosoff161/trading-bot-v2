# March 2026 Backtest Snapshot

Status: **LEGACY EXPLORATORY EVIDENCE**. Recorded in March 2026, before the
current validation bridge and paper lifecycle existed.

## Scope At The Time

The early prototype replayed public market candles across a small liquid-pair
universe. It used several rule families and fixed holding windows. The output
was useful as an engineering baseline, but it was not an independently
validated strategy study and must not be read as a current performance claim.

## What It Revealed

- A large share of candidate observations did not reach either modeled target
  within the chosen window. This made time-based closure a first-class
  measurement problem rather than a harmless default.
- The faster rule family was incompatible with a polling-based data path. A
  fast idea cannot be justified by a slow observation surface.
- Results varied substantially by regime and instrument. A pooled aggregate
  obscured that variation and could not justify a universal configuration.
- Early gain/loss labels were not enough: entry timing, exit geometry, fees,
  and maximum favorable/adverse excursion had to become explicit evidence.

## What Was Not Proven

The snapshot did not establish a durable edge, a deployable parameter set, or
expected profitability. It used a limited historical window, an early data
pipeline, and assumptions that were later replaced or constrained.

## Lasting Decision

Later work separated candidate generation from independent validation and
paper observation. Current candidates are not accepted because an early replay
looks favorable; they require the documented validation bridge and recorded
paper outcomes.
