# Main / TA Research Verdict Index

Status: **REFERENCE**. This is a high-level role boundary, not a current
strategy report or a source of performance claims.

Updated: 2026-06-11

## Bottom Line

The old directed 15m Main/TA engine is not the current source of alpha. Research
showed that it can sometimes point in the right direction, but the edge is thin,
late, unstable, and often eaten by execution costs or expired-entry artifacts.

Use Main/TA as:

- confirmation;
- invalidation;
- paper levels;
- risk context;
- visualization.

Do not use it as:

- a primary signal generator;
- a reason to place live orders;
- a replacement for scanner-led event context.

## Key Research Verdicts

| Report | Practical conclusion |
|---|---|
| Local historical research archive | Directed 15m Main is not accepted as a primary signal source. |
| Local historical research archive | Main WS is treated as confirmation/context, not anticipatory entry. |
| Local historical research archive | Realistic-fill and data-quality checks are mandatory before a claim. |
| Local historical research archive | No durable directional asymmetry is assumed without fresh evidence. |
| Local historical research archive | Lower timeframes are not presumed to improve an entry. |
| Local historical research archive | A result may be affected by fill or expiry artifacts and needs validation. |

## What Remains Useful

The research did not make the old code useless. It changed its role.

Useful pieces:

- indicators;
- regime labels;
- chart rendering;
- `SignalResult` snapshots;
- market structure summary;
- paper level generation;
- microstructure context when available.

Unsafe conclusions:

- "Main ENTRY means trade."
- "15m direction is enough."
- "Lower timeframe makes it earlier and better."
- "Backtest win rate proves live edge."

## Current Integration Rule

Scanner leads. Main confirms.

```text
scanner event thesis
  -> watch_queue
  -> TA snapshot
  -> confirm / invalidate / wait / expire
```

Any future `TRADE_PLAN_READY` result is a paper research artifact until separate
risk, execution, and live-money approvals exist.
