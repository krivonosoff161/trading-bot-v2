# Adaptive Paper Acceptance Cycle

Updated: 2026-07-10

## Goal

Run the rebuilt farm -> validator/PFR -> main-paper -> analyst/retest/memory
chain continuously for at least 24 hours and produce evidence that the cycle is
internally consistent. This is not a profitability claim and not live trading.

## Start

The acceptance wrapper creates a private baseline and then starts the ordinary
headless paper loop:

```bat
bat\paper_acceptance_headless_loop.bat
```

It explicitly keeps Telegram network delivery off, enables the bounded local
calculator mini-swarm and reviewer sidecars, and opens no dashboard or graph
viewer. Stop it with the existing farm stop wrapper.

## Status

```powershell
python -m scripts.strategy_lab.paper_acceptance status
```

The current report is written under the private research root:

```text
reports/paper_acceptance/<run_id>/baseline.json
reports/paper_acceptance/<run_id>/latest_report.json
```

## Acceptance Checks

- elapsed wall time is at least 24 hours;
- at least one new `PaperSignalLifecycle.v2` terminal row exists;
- no stored-entry/expired contradiction or negative hold count appears;
- append-only paper account replay exactly matches its snapshot;
- at least one scenario closes and produces a close card;
- lineage reports no conflicts or missing main/training joins;
- retest results progress, or there is no pending retest work;
- card/chart history remains reconstructible;
- farm errors stay zero and bounded artifact growth stays below 2 GiB per tracked artifact;
- `AUTO_TRADE` is not enabled and every observed farm/acceptance surface remains paper-only.

The run cannot pass early. A green preflight or a few successful cycles are not
a substitute for the 24-hour duration and new terminal evidence.
