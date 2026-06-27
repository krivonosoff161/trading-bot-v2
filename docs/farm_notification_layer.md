# Farm Visibility And Notification Layer

Status: **ACTIVE / GUARDED SURFACE**. Last updated: 2026-06-27.

The farm has visibility surfaces today, but Telegram is still not part of the
calculation or execution path. This document separates three things that are easy to
confuse:

- **Farm core:** `farm_loop` computes, validates, observes paper signals, and writes
  artifacts. It does not send Telegram messages.
- **Paper preview:** `paper_telegram_preview` renders offline operator cards from
  accepted paper instructions. It does not call Telegram and does not read tokens.
- **Product/scanner surfaces:** `start.bat` / `scripts.telegram_bot` and
  `scripts/ws/ws_main_screener.py` are separate operator notification or analyzer
  surfaces. They are not the owner of the farm/PFR/paper lifecycle.

## Principle

The farm's structured state is the source of truth:

- `state/farm_tasks.sqlite`
- `state/strategy_lab.sqlite`
- `state/derived/paper_signals*.json*`
- `state/derived/main_paper_*.json*`
- `state/derived/paper_telegram_preview.json`
- `logs/farm/*.jsonl`

Every dashboard, graph, report, or Telegram-facing artifact is a read-only consumer of
that state. Notifications are an output edge, never an input to compute or money.

## Current Surfaces

| Surface | Status | Authority |
|---|---|---|
| `farm_status_report` / dashboard | Implemented | Read-only status. |
| Obsidian graph/reports | Implemented/partial | Read-only summaries and links. |
| `paper_telegram_preview` | Implemented | Offline preview only, no network send by default. |
| `ws_main_screener.py` | Separate product surface | Sends scanner/operator alerts, not farm/PFR execution. |
| `start.bat` / Telegram analyzer | Separate product surface | Product analyzer, not Strategy Lab farm launcher; legacy `AUTO_TRADE`-gated auto-execute hook exists. |
| `scripts.analyze_chart` | Separate manual surface | Writes local chart/report analysis and can optionally send Telegram; not farm/PFR execution. |
| `scripts.run_latest_analysis` | Separate manual surface | Interactive wrapper that can reach `scripts.auto_execute` behind `AUTO_TRADE`; not a paper launcher. |
| `ws_scanner.py` | Legacy/diagnostic | Imports OKX client; do not use as canonical farm path. |

## Paper Telegram Preview

`paper_telegram_preview` reads accepted paper instructions after the chain:

```text
farm_loop --run-paper-signals
  -> main_paper_bridge
  -> main_paper_consumer
  -> main_paper_runtime_adapter
  -> main_paper_runtime
  -> paper_telegram_preview
```

The preview validates operator-card text and writes:

- `state/derived/paper_telegram_preview.jsonl`
- `state/derived/paper_telegram_preview.json`

It does not send a network request. It also does not promote a signal, mutate a queue, or
enable execution.

## Scanner And Analyzer Telegram

The scanner/analyzer Telegram code remains separate from the farm:

- `src.utils.llm_client` routes scanner/advisory LLM calls through `LLM_PROVIDER`
  (`alibaba` or `yandex`) and role-specific models.
- `src.utils.llm_formatter` is the Yandex-only chart/text formatter path used by the
  older Telegram analyzer surface. It does not follow the scanner `LLM_PROVIDER`
  router.
- `src.utils.telegram` owns token/chat lookup and message sending for surfaces that are
  explicitly started by the operator.

These paths can notify a human, but they must not enqueue farm tasks, consume PFR paper
instructions, or execute orders.

Important legacy boundary: `scripts/telegram_bot.py` still contains an
`AUTO_TRADE`-gated `scripts.auto_execute` hook for the old product flow. Therefore
`start.bat` must not be used as a Strategy Lab paper/PFR launcher. The current paper
chain uses `paper_telegram_preview` first; any real Telegram send must be added later
as a reviewed, opt-in read-only surface over derived paper artifacts.

Provider boundary: `LLM_PROVIDER=alibaba` proves the scanner/advisory provider path,
not the legacy Telegram chart analyzer. The chart analyzer must be audited separately
because it calls `llm_formatter.generate_client_text`, `generate_premium_analysis`, and
`generate_edu_text` through Yandex AI Studio.

Manual analyzer boundary: `scripts.analyze_chart` writes a report/snapshot/chart and
does not send Telegram unless `--send-telegram` is passed. `scripts.run_latest_analysis`
is more execution-adjacent: it is interactive and can import `scripts.auto_execute` after
an ENTRY result when `AUTO_TRADE` is enabled. Neither file is the farm/PFR paper runtime.

## Machine-Checkable Invariant

`python -m scripts.strategy_lab.operational_health` exposes
`telegram_delivery_flow`, `llm_surface_boundaries`, and `product_analyzer_boundary`:

- `farm_core_sends_telegram = false`
- `paper_sends_telegram_by_default = false`
- `execution_authority = false`
- `telegram_analyzer_current_for_farm = false`
- `telegram_analyzer_imports_auto_execute = true`
- `telegram_analyzer_auto_trade_guarded = true`
- `llm_surface_boundaries.telegram_chart_formatter_provider = yandex_only`
- `llm_surface_boundaries.telegram_chart_formatter_uses_llm_provider_env = false`
- `llm_surface_boundaries.telegram_chart_formatter_prompt_integrity = true`
- `llm_surface_boundaries.telegram_chart_formatter_mojibake_detected = false`
- `product_analyzer_boundary.analyze_chart_send_default = false`
- `product_analyzer_boundary.run_latest_analysis_imports_auto_execute = true`
- `product_analyzer_boundary.safe_for_farm_pfr_runtime = false`
- `scanner_surface_sends_to_subscribers = true` when the scanner surface exists
- `legacy_ws_scanner_uses_okx_client = true` when the legacy scanner file exists

The `telegram_delivery_ownership` readiness gate must stay `pass` before long paper/farm
runs. If it becomes blocked, someone blurred notification ownership and the run should
stop until the boundary is restored.

## Future Live Notification Rule

If real paper alerts are enabled later, they must remain a separate opt-in process or
flag:

- read-only over derived farm/paper artifacts;
- rate-limited and deduped;
- no `.env` writes, no `AUTO_TRADE`, no private account endpoints, no order calls;
- one alert per state change, not one alert per loop tick.
