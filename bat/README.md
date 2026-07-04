# bat/ - Batch Entry Points

Batch files in this folder are operator shortcuts. They assume the working directory is
the repository root (`cd /d %~dp0\..`).

## Current Canonical Strategy Lab Path

- `strategy_lab_farm_full_cycle_loop.bat` - visible continuous farm cycle:
  scanner/watch intake -> farm lifecycle -> compute worker -> hard validation -> paper
  runtime plus PFR-backed paper-signal watch lane.
- `strategy_lab_farm_full_cycle_stop.bat` - writes the stop-file for the loop above.
- `strategy_lab_control_room.bat` - opens visible farm, dashboard, graph, and status
  monitor windows for operator runs.
- `strategy_lab_status_monitor.bat` - read-only periodic status loop; exits when the
  farm stop-file appears.
- `strategy_lab_status.bat` - read-only operator status.

The canonical path is paper/research only: no `.env`, no `AUTO_TRADE`, no orders, no
private exchange endpoints, no Telegram.

## Legacy / Diagnostic

Older research/universe/scanner-farm batch files remain for diagnostics and historical
comparison. Do not use them as the default operator path unless a task explicitly asks for
that legacy lane.
