# Main Impulse Rebuild Report - 23.05.2026

Branch: `feature/main-impulse-rebuild`

## Added / Changed

- `src/strategy/signal_engine.py`
  - Fixed B1 daily `vwap` / `day_high` / `day_low` rounding by replacing fixed `round(x, 4)` with `_round_price(x, close)`.
  - Added optional ride rendering in the Telegram fallback formatter when `engine_vars.exit_rule.type == "ride"`.
  - Exposes optional `exit_rule` in analysis snapshots if a future `SignalResult` carries it.

- `src/strategy/signal_contract.py`
  - Added immutable `SignalContract`, `ExitRule`, and `FollowRule` dataclasses.

- `src/data/main_impulse_config.py`, `src/data/main_impulse_engine.py`, `src/data/main_impulse_records.py`, `scripts/ws/ws_main_impulse.py`
  - Added a separate paper-only main impulse process.
  - Uses its own `main_impulse` config namespace and `logs/main_impulse/`.
  - Keeps one owner per pair until close.
  - Supports `ride` exits by structure break and `scaled` exits by impulse target.
  - Has paper-only guards: `AUTO_TRADE` must be false, `main_impulse.auto_trade=false`, `main_impulse.paper=true`.

- `config.yaml`
  - Added `main_impulse:` namespace with `enabled: false` by default.
  - Existing `impulse_pump:` config was not changed.

- `src/data/snapshot_writer.py`, `scripts/analysis/analysis_query.py`, `scripts/build_journal.py`
  - Added optional `exit_rule` propagation.
  - Existing records without `exit_rule` remain valid.
  - Journal now has an additional `Main Impulse` sheet for new paper logs.

- `src/utils/llm_formatter.py`
  - Added an additive ride branch for `exit_rule.type == "ride"`.
  - Existing fixed TP1/TP2 text remains the fallback when `exit_rule` is absent.

## Backward Compatibility

- `ws_main_screener.py`, `ws_impulse_pump.py`, `telegram_bot.py`, `ws_bb_fade.py`, `ws_screener_live.py`, and `tape_recorder.py` were not replaced.
- The currently running `impulse_pump` process keeps its old code, config namespace, and log directory.
- New `main_impulse` is disabled by default and does not affect `start_all.bat`.
- `exit_rule` is optional in snapshots and journal loaders, so historical JSONL records render as before.
- The B1 rounding fix only preserves significant precision for low-price instruments; higher-price rounding behavior remains effectively unchanged.

## Run Commands

- Config safety check:
  - `python scripts/ws/ws_main_impulse.py --check-config`

- Start the new paper process manually:
  - set `main_impulse.enabled: true` in `config.yaml`
  - keep `main_impulse.paper: true`
  - keep `main_impulse.auto_trade: false`
  - keep environment `AUTO_TRADE=false`
  - run `python scripts/ws/ws_main_impulse.py`

## Rollback

- Disable the new process:
  - set `main_impulse.enabled: false`

- Remove the additive code from runtime use:
  - do not run `scripts/ws/ws_main_impulse.py`

- Full code rollback for this branch:
  - revert the files listed in this report.

## Verification

- `python scripts/ws/ws_main_impulse.py --check-config`
- `python -m py_compile` on all touched Python files.
- Mini-smoke imports for:
  - `scripts.ws.ws_main_screener`
  - `scripts.ws.ws_impulse_pump`
  - `scripts.telegram_bot`
  - `scripts.ws.ws_bb_fade`
  - `scripts.ws.ws_screener_live`
  - `scripts.analysis.tape_recorder`
- `python -m pytest` did not run tests in this environment: pytest reported `collected 0 items` and then failed in output capture cleanup.
- `python -m pytest tests -q` currently fails during collection on existing test import `scripts.ws.ws_pump_orchestrator`, which is not present under `scripts/ws/`.

## Honesty / Risk

- No live forward run was started.
- No orders are possible through the new process as written; it has no exchange order path.
- The new main impulse logic is paper validation infrastructure, not a proven live-money engine.
