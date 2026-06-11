# Main / TA Research Verdict Index

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
| `scripts/analysis/research/docs/main_directional_FINAL_verdict_2026-05-31.md` | Directed 15m Main is exhausted as a primary money edge; direction is not enough |
| `scripts/analysis/research/docs/main_entry_regime_forensics_2026-05-29.md` | Main WS behaves like late momentum confirmation, not anticipatory entry |
| `scripts/analysis/research/docs/anticipation_geometry_toggle_2026-05-29.md` | Realistic-fill baseline is deeply negative; RR geometry did not rescue it |
| `scripts/analysis/research/docs/directional_asymmetry_digest_2026-05-30.md` | No causal condition produced durable OOS directional asymmetry |
| `scripts/analysis/research/docs/lower_tf_sweep_2026-05-30.md` | Lower timeframes worsened the cost/edge ratio |
| `scripts/analysis/research/docs/all_signals_tf_breakdown_2026-05-30.md` | Some wins were phantom/expired-entry artifacts |
| `docs/strategy_impulse_postmortem.md` | `ws_main_impulse` forward paper failed |

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
