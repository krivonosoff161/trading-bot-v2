# Farm Visibility And Notification Layer

Status: **ACTIVE / GUARDED SURFACE**. Last updated: 2026-06-27.

The farm has visibility surfaces today, but Telegram is still not part of the
calculation or execution path. This document separates three things that are easy to
confuse:

- **Farm core:** `farm_loop` computes, validates, observes paper signals, and writes
  artifacts. It does not send Telegram messages.
- **Paper preview:** `paper_telegram_preview` renders offline operator cards from
  accepted paper instructions. It does not call Telegram and does not read tokens.
- **Paper sender:** `paper_telegram_sender` dry-runs delivery over preview artifacts
  by default and sends only with explicit `--send` to `PAPER_CHAT_ID`.
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
- `state/derived/paper_telegram_delivery.json`
- `logs/farm/*.jsonl`

Every dashboard, graph, report, or Telegram-facing artifact is a read-only consumer of
that state. Notifications are an output edge, never an input to compute or money.

## Current Surfaces

| Surface | Status | Authority |
|---|---|---|
| `farm_status_report` / dashboard | Implemented | Read-only status. |
| Obsidian graph/reports | Implemented/partial | Read-only summaries and links. |
| `paper_telegram_preview` | Implemented | Offline preview only, no network send by default. |
| `paper_telegram_sender` | Implemented | Dry-run by default; optional `--send` only to `PAPER_CHAT_ID`. |
| `ws_main_screener.py` | Separate product surface | Sends scanner/operator alerts, not farm/PFR execution. |
| `start.bat` / Telegram analyzer | Separate product surface | Product analyzer, not Strategy Lab farm launcher; legacy auto-execute hook requires both `TELEGRAM_BOT_ALLOW_AUTO_EXECUTE=1` and `AUTO_TRADE`. |
| `scripts.analyze_chart` | Separate manual surface | Writes local chart/report analysis and can optionally send Telegram; not farm/PFR execution. |
| `scripts.run_latest_analysis` | Separate manual surface | Interactive wrapper that can reach `scripts.auto_execute` only behind `AUTO_TRADE` plus explicit `RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1`; not a paper launcher. |
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

## Paper Telegram Sender

`paper_telegram_sender` reads only the preview artifact and writes:

- `state/derived/paper_telegram_delivery.jsonl`
- `state/derived/paper_telegram_delivery.json`

The delivery snapshot is an audit surface, not just a send log. Its per-item statuses
explain why alerts did or did not leave the machine, for example `dry_run`,
`skipped_no_paper_chat`, `invalid_preview`, `skipped_no_token`, or `error`. The fast
`operational_health` preflight surfaces this as `paper_telegram_delivery_breakdown`,
so an operator does not need to inspect JSON by hand to know whether Telegram is
unconfigured, intentionally dry-run, or failing.

The delivery snapshot must be current against `paper_telegram_preview.json`. If the
preview artifact is newer than the delivery snapshot, `operational_health` reports
`paper_telegram_sender_available = warn` and the sender dry-run must be repeated before
an operator treats Telegram status as reviewed.

Default mode is dry-run:

```bash
python -m scripts.strategy_lab.paper_telegram_sender --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Network delivery requires explicit `--send` and existing `TELEGRAM_BOT_TOKEN` plus
`PAPER_CHAT_ID`:

```bash
python -m scripts.strategy_lab.paper_telegram_sender --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --send
```

It never falls back to `TELEGRAM_CHAT_ID`, never reads farm queues as input, and never
enables execution.

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
execution-adjacent `scripts.auto_execute` hook for the old product flow, but
`AUTO_TRADE` alone is no longer enough to import or call it. The Telegram analyzer
requires the explicit legacy opt-in `TELEGRAM_BOT_ALLOW_AUTO_EXECUTE=1` and then the
old `AUTO_TRADE` guard must also be true. Therefore `start.bat` must not be used as a
Strategy Lab paper/PFR launcher. The current paper chain uses
`paper_telegram_preview` first; real paper Telegram delivery is available only through
the reviewed, opt-in `paper_telegram_sender` surface over derived paper artifacts.

Provider boundary: `LLM_PROVIDER=alibaba` proves the scanner/advisory provider path,
not the legacy Telegram chart analyzer. The chart analyzer must be audited separately
because it calls `llm_formatter.generate_client_text`, `generate_premium_analysis`, and
`generate_edu_text` through Yandex AI Studio.

The machine-readable formatter status is intentionally sanitized. It reports only
provider shape (`provider=yandex`, `provider_scope=yandex_only`, key presence booleans,
sanitized `model_label`, and no Telegram/execution authority). It must never expose API
keys, folder ids, chat ids, prompts, or request payloads. A mismatch such as
`LLM_PROVIDER=alibaba` plus `scanner_formatter_provider_mismatch=true` is expected until
the product analyzer is migrated through a dedicated adapter review. The text-only chart
card can now be tested through the shared router with an explicit opt-in:
`PRODUCT_ANALYZER_LLM_ROUTER=llm_client`. That opt-in covers only
`generate_client_text`; premium screenshot analysis and educational Q&A remain on the
legacy Yandex formatter path until they receive separate prompt/provider reviews.

Manual analyzer boundary: `scripts.analyze_chart` writes a report/snapshot/chart and
does not send Telegram unless `--send-telegram` is passed. `scripts.run_latest_analysis`
is more execution-adjacent: it is interactive and can import `scripts.auto_execute` after
an ENTRY result only when `AUTO_TRADE` is enabled and
`RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1` is also set. Neither file is the farm/PFR
paper runtime.

## Machine-Checkable Invariant

`python -m scripts.strategy_lab.operational_health` exposes
`telegram_delivery_flow`, `llm_surface_boundaries`, and `product_analyzer_boundary`:

- `farm_core_sends_telegram = false`
- `paper_sends_telegram_by_default = false`
- `paper_sender_cli = scripts.strategy_lab.paper_telegram_sender`
- `paper_sender_chat_env = PAPER_CHAT_ID`
- `execution_authority = false`
- `telegram_analyzer_current_for_farm = false`
- `telegram_analyzer_imports_auto_execute = true`
- `telegram_analyzer_auto_trade_guarded = true`
- `telegram_analyzer_requires_auto_execute_opt_in = true`
- `llm_surface_boundaries.telegram_chart_formatter_provider = yandex_only`
- `llm_surface_boundaries.telegram_chart_formatter_status.schema = llm_formatter_provider.v1`
- `llm_surface_boundaries.telegram_chart_formatter_configured = true/false`
- `llm_surface_boundaries.telegram_chart_formatter_uses_llm_provider_env = false`
- `llm_surface_boundaries.scanner_formatter_provider_mismatch = true` when scanner
  `LLM_PROVIDER` differs from the legacy chart formatter provider
- With `PRODUCT_ANALYZER_LLM_ROUTER=llm_client`,
  `llm_surface_boundaries.telegram_chart_formatter_provider = shared_llm_client_opt_in`,
  `telegram_chart_formatter_uses_llm_provider_env = true`, and
  `scanner_formatter_provider_mismatch = false` when the shared router follows the same
  `LLM_PROVIDER`.
- `llm_surface_boundaries.telegram_chart_formatter_prompt_integrity = true`
- `llm_surface_boundaries.telegram_chart_formatter_mojibake_detected = false`
- `product_analyzer_boundary.analyze_chart_send_default = false`
- `product_analyzer_boundary.run_latest_analysis_imports_auto_execute = true`
- `product_analyzer_boundary.run_latest_analysis_requires_auto_execute_opt_in = true`
- `product_analyzer_boundary.safe_for_farm_pfr_runtime = false`
- `scanner_surface_sends_to_subscribers = true` when the scanner surface exists
- `legacy_ws_scanner_uses_okx_client = true` when the legacy scanner file exists

The `telegram_delivery_ownership` readiness gate must stay `pass` before long paper/farm
runs. If it becomes blocked, someone blurred notification ownership and the run should
stop until the boundary is restored.

## Future Live Notification Rule

If real paper alerts are enabled, they must remain the separate opt-in sender process or
flag:

- read-only over derived farm/paper artifacts;
- `PAPER_CHAT_ID` only, never scanner/default chats;
- rate-limited and deduped;
- no `.env` writes, no `AUTO_TRADE`, no private account endpoints, no order calls;
- one alert per state change, not one alert per loop tick.
