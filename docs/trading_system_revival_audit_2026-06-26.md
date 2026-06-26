# Trading System Revival Audit - Farm/Paper/Main Boundary

Status: **ACTIVE operator audit**. Date: 2026-06-26.

This document records the current runtime truth after auditing the farm, PFR bridge,
paper runtime, paper-signal lane, main scanner, Telegram, journals, and runbooks. It is
not an edge claim and not a live-trading activation.

## Safety Boundary

The restored operator path is still paper/research only:

- public OKX market data only;
- no `.env` edits;
- no `AUTO_TRADE`;
- no order execution;
- no private exchange/account endpoints;
- Telegram is a surface, not a decision or execution layer.

## Canonical Data Flow

```text
OKX public discovery / scanner-watch intake
  -> scripts.strategy_lab.farm_loop
  -> state/farm_tasks.sqlite
  -> state/strategy_lab.sqlite compute queue
  -> worker jobs
  -> candidate classification
  -> hard validation
  -> setup_library cards
  -> scripts.strategy_lab.paper_loop / paper_runtime
  -> paper/paper_trades.jsonl

state/strategy_lab.sqlite PFR records
  -> paper_signals PFR bridge (only when --pfr-db-path is explicit)
  -> state/derived/paper_signals.jsonl
  -> visual paper-signal reviews and status snapshots
```

## Priority And Ownership

| Layer | Current role | Priority | Notes |
|---|---|---:|---|
| `farm_loop.py` | Canonical research/paper cycle | 1 | Plans, prepares, enriches, sweeps, validates, and runs paper lanes. |
| `paper_runtime.py` / `paper_loop.py` | Gated replay paper runtime | 2 | Reads only `PAPER_FORWARD_READY` setup cards. |
| `paper_signals/` | Operational forward-watch lane | 2 | Observes generated/PFR paper signals, writes JSONL/snapshots/reviews. |
| `pfr_bridge.py` | Farm validation -> paper-watch bridge | 2 | Active only with explicit `--pfr-db-path`; now wired into the visible wrapper. |
| `ws_main_screener.py` | Separate scanner/Telegram runtime surface | 3 | Does not consume farm/PFR outputs today. Do not treat it as the farm executor. |
| Telegram | Notification surface | 4 | Env-gated; not part of the decision path. |
| Excel journal | Reporting/training artifact | 4 | Rebuild is safe by default; private fills require explicit opt-in. |

## Findings

### F1 - PFR bridge existed but was not active in the visible full-cycle wrapper

`farm_loop.py` and `paper_signals_run.py` already supported `--pfr-db-path`, and
`pfr_bridge.py` correctly reads validated farm records from `state/strategy_lab.sqlite`.
However, `bat/strategy_lab_farm_full_cycle_loop.bat` did not pass that path. A long
visible run therefore executed the paper-signal lane without the PFR-backed setup source.

Fix: the wrapper now sets `STRATEGY_LAB_PFR_DB_PATH` to
`%TRADING_BOT_RESEARCH_ROOT%\state\strategy_lab.sqlite` by default and passes it to
`farm_loop --run-paper-signals`.

### F2 - Runbook smoke command used stale flags

`docs/farm_runbook.md` contained an old full-cycle smoke command with flags such as
`--max-enrich-funding`, `--max-enrich-oi`, `--max-validation`, and `--max-paper`. The
current CLI exposes `--max-enrich`, `--max-worker-jobs`, and `--max-paper-cards` instead.

Fix: the smoke and continuous commands now use the real CLI surface and include
`--pfr-db-path`.

### F3 - Main engine is not the farm/paper executor

The old/main WebSocket scanner can still produce Telegram-facing signal analysis, but it
does not read farm setup cards, PFR records, or paper-signal outcomes. That is intentional
until a separate bridge contract is designed and tested. The current safe priority is:

1. farm/PFR/paper-watch proves forward behavior;
2. journals and reviews preserve outcomes;
3. only then may a main-engine integration contract be designed.

## Operator Commands

Preflight:

```bash
python -m scripts.strategy_lab.operational_health \
  --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" \
  --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

Bounded full-cycle smoke:

```bash
python -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --paper-signals-max-observe 0 --paper-signals-max-pfr-scan 1 --paper-signals-fetch-timeout 3 --max-plan-events 1 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-paper-cards 1 --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Visible continuous run:

```bash
bat\strategy_lab_farm_full_cycle_loop.bat
```

Stop:

```bash
bat\strategy_lab_farm_full_cycle_stop.bat
```

Status:

```bash
python -m scripts.strategy_lab.status
python -m scripts.strategy_lab.farm_status_report
python -m scripts.strategy_lab.operational_health --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite"
```

## Remaining Work

These are intentionally not done in this pass:

1. Main-engine bridge contract: define how a farm/PFR/paper setup becomes a main-engine
   paper instruction without importing the old live/order path.
2. Telegram paper channel: audit text, chart rendering, and notification routing before
   enabling paper-signal alerts.
3. Journal modernization: map paper-signal/PFR outcomes into a training-friendly schema
   and keep private account fills opt-in.
4. Dead-code retirement: archive/delete only after import and command references are
   proven unused by tests and docs.

The current pass restores a coherent full-cycle operator path. It does not claim that the
full trading product is complete.

## Verification On 2026-06-26

Commands run after the wrapper/runbook fix:

```bash
cmd /c bat\strategy_lab_farm_full_cycle_loop.bat
```

with `STRATEGY_LAB_FARM_DRY_RUN=1`, `STRATEGY_LAB_FARM_ONCE=1`, and small caps. Result:
the wrapper built a valid `farm_loop` command, printed the resolved PFR DB path, and exited
with code 0.

```bash
python -X utf8 -m scripts.strategy_lab.farm_loop --once --apply --run-worker --run-validation --run-paper --run-paper-signals --enrich-funding --enrich-oi --pfr-db-path "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab\state\strategy_lab.sqlite" --paper-signals-max-observe 0 --paper-signals-max-pfr-scan 1 --paper-signals-fetch-timeout 3 --max-plan-events 1 --max-prepares 1 --max-enrich 1 --max-sweeps 1 --max-worker-jobs 1 --max-paper-cards 1 --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Result: bounded apply smoke completed in paper/research mode. It found active work,
checked paper readiness, and kept the live/money boundary closed.

```bash
python -m scripts.strategy_lab.farm_status_report --json
```

Important observed counts:

- `pfr_bridge.records_loaded = 53`
- `pfr_bridge.passed_quality = 43`
- `pfr_bridge.unique_setups = 11`
- `pfr_bridge.risk_too_wide = 26`
- `paper_signals.total = 637`
- `paper_signals.armed = 27`
- `paper_signals.opened_paper = 12`
- `paper_signals.reviewed = 598`

Targeted tests:

- `tests/test_pfr_bridge.py tests/test_paper_signals.py tests/test_operational_health.py tests/test_build_journal_safety.py`: 87 passed.
- `tests/test_farm_loop_integration.py tests/test_paper_runtime.py tests/test_paper_contract.py`: 65 passed, 1 pre-existing CUDA warning.
- `git diff --check`: clean.
