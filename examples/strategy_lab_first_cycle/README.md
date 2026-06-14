# Strategy Lab first controlled cycle

This is a small public showcase of the Strategy Lab operating loop. It is not a
strategy recommendation, not a signal, and not a profitability claim. Full run
artifacts, candidate tables, SQLite state, Obsidian notes, and follow-up specs
remain in the private research root.

## What was tested

The first unattended controlled loop was run against already-prepared private
research data using:

- the closed proposal queue;
- the deterministic worker;
- the validator-lite labels;
- the private candidate registry;
- no live trading;
- no order engine;
- no paid LLM calls;
- no market-data downloads during the loop.

The loop used the default safe desktop policy. It was intended to prove that the
machine can work unattended, keep the queue bounded, write artifacts, and stop
cleanly.

## Public-safe summary

| Item | Result |
|---|---:|
| Wall-clock loop duration | 240 minutes |
| Loop iterations | 240 |
| Completed worker jobs | 8 |
| Deferred worker checks | 232 |
| Worker failures | 0 |
| Missing-data skips | 0 |
| LLM requests | 0 |
| LLM cost | 0 |
| New candidate rows | 33 |
| Unique new candidates | 23 |
| Forward-paper labels | 7 |
| Observe labels | 11 |
| Reject labels | 5 |

The strongest public-safe cluster from this cycle was mean-reversion-style
research on high-beta crypto symbols. This is only a pointer for further
pressure testing; it is not an executable strategy.

## What the cycle proved

- The queue and worker ran without crashes.
- The throttle worked: the loop waited instead of flooding the desktop.
- The private registry and reports were updated.
- The loop stopped by its configured safety ceiling.
- The system did not spend money or touch live-trading code.

## Known limitation found

The first long run revealed an intentional safety backstop in
`scripts/strategy_lab/research_loop.py`: requested durations are clamped to four
hours. This is safe, but it means an "overnight" run currently requires either
manual restart or a future explicit overnight mode.

Lower timeframes also remain blocked until the worker is fully timeframe-aware.
This cycle therefore stayed on the safer daily-data path.

## How to reproduce the shape

Use the operator guide rather than this example as an exact command source:

- [Strategy Lab operator guide](../../docs/strategy_lab_operator_guide.md)

The public command shape is:

```bash
python -m scripts.strategy_lab.research_loop --apply --duration-minutes 240 --sleep-seconds 60 --max-queued 10 --max-worker-jobs-per-iteration 1
```

The private result corpus is intentionally not included in the public repository.
