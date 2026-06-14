# Strategy Lab Remaining Work

Date: 2026-06-14.

This document tracks what is still required before the Strategy Lab can be treated
as a low-surprise overnight research machine. The current code is a controlled,
bounded research runner. It is not a profit engine, not a live-trading system, and
not a fully autonomous daemon.

## Current Baseline

Implemented and verified:

- Private-root state, SQLite queue, worker, dashboard/status, and operator bats.
- Runtime resource policy: worker throttle, max jobs per hour, max variants per job.
- Strategy registry, validator-lite, reducer, entry-timing metrics, and candidate registry.
- Event-driven research cycle and bounded research loop.
- 1m microscope and demand-driven 1m data preparation.
- OKX public-only 1m market-data provider.
- Advisory LLM proposal loop with explicit gates, daily cap, usage log, JSON guardrails,
  and a contract breaker for repeated malformed responses.
- Worker singleton lock and active queue dedup guard.
- Safe default: no live trading, no order engine, no paid LLM, no automatic network fetch.

Latest verification before this note:

- `python -m pytest -q` -> 644 passed.
- Targeted ruff checks -> clean.
- `git diff --check` -> clean except normal CRLF warnings.
- Temporary-root smoke run completed with LLM disabled and `cost_rub=0.0`.

## P0 Before Confident Overnight Runs

1. **Timeframe contract in `ExperimentSpec`**

   Problem: readiness can be timeframe-aware, but execution still has paths where
   the worker ultimately resolves candle files by symbol and file size. Once 15m,
   1h, or 4h files exist next to 1d files, this can silently run a spec on the wrong
   timeframe.

   Required work:

   - Add an explicit timeframe field to `ExperimentSpec`.
   - Preserve `Proposal.requested_timeframe` during compile.
   - Make `choose_symbol_file()` timeframe-aware.
   - Worker must return/defer `DATA_NOT_READY` instead of running with a wrong file.
   - Tests: 15m spec with only 1d data must not execute.

2. **Market-data loader for 15m / 1h / 4h / 1d**

   Problem: the real public provider currently prepares 1m only. The lab needs
   multiple timeframes:

   - `1d`: regime and broad context.
   - `4h` / `1h`: setup testing.
   - `15m`: entry timing.
   - `1m`: event microscope only, not full sweeps.

   Required work:

   - Extend the public OKX provider to fetch capped 15m, 1h, 4h, and 1d candles.
   - Keep the same safety model: public market-data only, no keys, no order/account
     endpoints, no full-market downloads, private-root writes only.
   - Add readiness checks and tests per timeframe.

3. **Operator-grade overnight command**

   Problem: the pieces are safe, but the operator still has to remember command
   details.

   Required work:

   - Add a safe no-LLM overnight bat.
   - Keep paid LLM overnight as explicit opt-in only.
   - Print the private root, expected duration, queue cap, worker cap, LLM state,
     and next morning status command before starting.
   - Do not auto-enable paid provider env.

4. **Graceful stop and requeue-now**

   Problem: force-kill is survivable but not ideal. A quick restart after a killed
   worker can leave a job in `running` until stale timeout.

   Required work:

   - Add a stop command that writes intent and lets the current job finish.
   - Add an explicit maintenance command to requeue stale/running jobs when the
     operator confirms the worker is not alive.
   - Status/dashboard should show worker lock and stale-running hints.

## P1 Quality And Morning Review

5. **Morning report**

   Required work:

   - One command that summarizes the last overnight run:
     - jobs completed/deferred/failed;
     - strategies and symbols tested;
     - new candidates by verdict;
     - rejects and reasons;
     - LLM requests/tokens/cost;
     - data missing by timeframe;
     - recommended next command.

6. **Tiny real LLM live test**

   Required work:

   - Run Alibaba/Qwen with a 1-2 RUB cap and one or two iterations.
   - Verify JSON contract, validation results, usage accounting, and contract breaker.
   - Do not use the overnight LLM bat until this passes.

7. **Proposal quality scoring**

   Required work:

   - Track why LLM proposals were useful or rejected.
   - Separate bad JSON, unknown strategy, wrong timeframe, heavy job, missing data,
     duplicate candidate, and unsafe field in reports.
   - Use this to tune prompts, not to bypass code validation.

## P2 Later Work

8. **GPU backend**

   Keep future-only until the CPU path is stable. GPU should be a batch accelerator,
   not a separate decision path.

9. **Richer dashboard**

   Add queue/history charts, per-timeframe data coverage, and candidate drilldown.
   Keep private result data out of the public repository.

10. **More strategies and parameter families**

   Add only after the execution/data contract is reliable. The current priority is
   clean measurement, not more variants.

## Safe Commands Today

No paid LLM:

```powershell
cd C:\Users\krivo\trading-bot-v2
python -m scripts.strategy_lab.research_loop --apply --night-mode --duration-minutes 480 --sleep-seconds 60 --max-queued 20 --max-worker-jobs-per-iteration 1
```

Status:

```powershell
python -m scripts.strategy_lab.status
```

Do not use paid LLM overnight until the tiny live test passes.
