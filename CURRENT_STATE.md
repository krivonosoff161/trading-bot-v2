# Current State

Status: **ACTIVE**. Updated 2026-07-11.

This page states what the public repository supports. It is deliberately not a
runtime dashboard: process IDs, current balances, private journals, signal
rows, provider state, and local findings belong outside public Git.

## Supported Today

- A calculation farm schedules bounded paper/research tasks over public market
  data, then classifies and exports eligible candidates.
- The `honest-backtest` bridge supplies independent validation evidence and
  stamps verdicts back into the local lifecycle.
- The paper runtime records observation and outcomes without exchange order
  authority.
- A separate preview/delivery edge can render paper cards only when explicitly
  configured; it is not a farm decision-maker.
- Scanner/news intake can provide upstream context. It is not the canonical
  source of trade authority.
- The adaptive research-center contracts are implemented as paper-only,
  versioned candidate flows. They create inspectable next-test requests; they
  do not silently tune a model or promote a strategy.
- New validation requests fail closed unless they carry an untouched evaluation
  epoch distinct from the farm selection data. Existing selection-only evidence
  therefore needs fresh data before it can become paper-forward ready.

## Explicitly Not Supported

- Live trading, real-money orders, or exchange-account actions.
- A profitability claim, a calibrated signal service, or investment advice.
- LLM authority to change parameters, validation verdicts, paper readiness, or
  execution permissions.
- Publishing private data, candidate rankings, raw strategy calculations,
  provider prompts/responses, logs, or credentials.

## Known Operational Limits

| Limit | Current behavior |
|---|---|
| Market data | The system can only reason over available public/local data. Missing OI or microstructure remains an explicit gate, not a guessed value. |
| Validation | A passing historical result is evidence only; paper outcomes are still needed. |
| LLM providers | Providers are optional and disabled/limited by configuration; deterministic code remains authoritative. |
| Telegram | Delivery is opt-in and deduplicated; it must never be mistaken for execution. |
| Legacy surfaces | Old engine, `start_all.bat`, and execution-adjacent scripts are isolated references, not supported farm paths. |

## How To Verify A Local Run

Use the read-only status command first:

```powershell
bat\strategy_lab_status.bat
```

Then follow [Farm Runbook](docs/farm_runbook.md). Do not copy private output
into an issue, PR, or public document; produce a sanitized aggregate instead.

## Next Public Work

1. Complete the documentation and Git-hygiene rebuild.
2. Accumulate bounded paper evidence and review it locally.
3. Run the independent review and private acceptance window for issue #172
   before using any environment candidate as research input.

The development sequence is maintained in [ROADMAP.md](ROADMAP.md).
