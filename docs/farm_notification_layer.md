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
  by default. The canonical farm loop runs that dry-run after preview generation so
  delivery status stays current; network sending still requires explicit `--send` to
  active Telegram bot subscribers/superadmins from `scripts/subscriptions.json`.
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
| `paper_telegram_sender` | Implemented | Dry-run by default; optional `--send` only to active subscriber bot chats. |
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
  -> paper_telegram_sender dry-run audit
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
- `state/derived/paper_telegram_delivery_outbox.json`
- `state/derived/paper_telegram_delivery.lock`

The delivery snapshot is an audit surface, not just a send log. Its per-item statuses
explain why alerts did or did not leave the machine, for example `dry_run`,
`skipped_no_subscribers`, `skipped_duplicate`, `invalid_preview`, `skipped_no_token`, or `error`. The fast
`operational_health` preflight surfaces this as `paper_telegram_delivery_breakdown`,
so an operator does not need to inspect JSON by hand to know whether Telegram is
unconfigured, intentionally dry-run, or failing.

The delivery snapshot must be current against `paper_telegram_preview.json`. The
canonical `farm_loop --run-paper-signals` path refreshes it in dry-run mode immediately
after preview generation. If a preview is regenerated manually or by another tool and
becomes newer than the delivery snapshot, `operational_health` reports
`paper_telegram_sender_available = warn` and the sender dry-run must be repeated before
an operator treats Telegram status as reviewed. Delivery artifacts store recipient
hashes, not raw chat ids.

Current delivery hardening: chart photos are counted as sent only when Telegram returns
a photo message id. HTTP/`ok=false` photo failures are surfaced as delivery errors
instead of being silently treated as successful chart sends. The sender writes a
delivery outbox claim before an injected transport call and holds a no-follow OS lock
across the whole external side-effect boundary. A second process reports
`pending_delivery_claim` without calling its transport. The primary delivery key binds
immutable card content identity to the pseudonymous recipient; legacy signal/preview
keys remain read-compatible for completed deliveries.

An unreadable, malformed, or structurally invalid existing outbox produces
`outbox_unavailable` and is never replaced by an empty recovery state. The legacy
`paper_telegram_sent_keys.json` compatibility index follows the same fail-closed rule;
an existing unreadable or invalid index cannot be treated as an empty delivery history.
Atomic JSON writes flush the temporary file before replace and flush the parent
directory where the platform supports directory handles. A photo message id is
recorded as `external_ack_ambiguous` through that boundary before the text call begins.
The row keeps separate `photo_status` and `text_status` values, so a failed text
acknowledgement cannot turn a confirmed photo acknowledgement into a retryable
whole-card send. Later runs fail closed on ambiguous or crash-left `pending` records.
This is not an exactly-once Telegram guarantee; it is a recovery boundary that
prevents automatic duplicate sends after ambiguous external acknowledgements.

Default mode is dry-run:

```bash
python -m scripts.strategy_lab.paper_telegram_sender --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab"
```

Network delivery requires explicit `--send`, existing `TELEGRAM_BOT_TOKEN`, and active
subscriber/superadmin records:

```bash
python -m scripts.strategy_lab.paper_telegram_sender --private-root "%USERPROFILE%\github_projects\trading-bot-research\strategy-lab" --send
```

It never falls back to `TELEGRAM_CHAT_ID`, never reads farm queues as input, and never
enables execution.

Paper setup cards are human-readable but still bounded. They include setup family,
entry, stop, targets, max hold, source/verdict provenance, and the required
`research-only, not an order` plus `execution_allowed=false` boundary.

## Scanner And Analyzer Telegram

The scanner/analyzer Telegram code remains separate from the farm:

- `src.utils.llm_client` routes scanner/advisory LLM calls through `LLM_PROVIDER`
  (`alibaba` or `yandex`) and role-specific models.
- `src.utils.llm_formatter` is the Yandex-only chart/text formatter path used by the
  older Telegram analyzer surface. It does not follow the scanner `LLM_PROVIDER`
  router.
- `src.utils.telegram` owns token/chat lookup and message sending for surfaces that are
  explicitly started by the operator.

Scanner/news notifications are the public channel surface for market context and
tokenomics-style items. The scanner prefers `TELEGRAM_NOTIFICATION_CHAT_ID` and falls
back to the legacy `SCANNER_CHAT_ID`; logs expose only `target_count`/status, never raw
chat ids. These notifications must not carry full paper setup levels or private strategy
calculations.

These paths can notify a human, but they must not enqueue farm tasks, consume PFR paper
instructions, or execute orders.

Current Telegram analyzer UX: `scripts.telegram_bot` exposes `Анализ`, `VIP`, and
`Обучение` in the persistent bottom keyboard. `Анализ` now opens bounded pair
categories instead of dumping the whole active universe at once; `VIP` and `Обучение`
route to their existing premium/educational handlers; superadmins also see an
admin-only command helper. This is a product convenience layer only. The farm/PFR paper
loop still owns paper instructions, paper previews, delivery dry-runs, and training
exports.

Current correction: the admin menu also exposes a superadmin-only read-only farm status
button. It reads `farm_cockpit.build_cockpit()` and must not start/stop loops, send
paper alerts, read secrets, or call exchange/order code.

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
because it calls `llm_formatter.generate_client_text`, `generate_edu_text`, and
`generate_premium_analysis`; only the text-only entrypoints can use the shared router.

The machine-readable formatter status is intentionally sanitized. It reports only
provider shape (`provider=yandex`, `provider_scope=yandex_only`, key presence booleans,
sanitized `model_label`, and no Telegram/execution authority). It must never expose API
keys, folder ids, chat ids, prompts, or request payloads. A mismatch such as
`LLM_PROVIDER=alibaba` plus `scanner_formatter_provider_mismatch=true` is expected until
the product analyzer is migrated through a dedicated adapter review. The text-only chart
card can now be tested through the shared router with an explicit opt-in:
`PRODUCT_ANALYZER_LLM_ROUTER=llm_client`. That opt-in covers the text-only
`generate_client_text` and `generate_edu_text` entrypoints; premium screenshot
analysis remains on the legacy Yandex formatter path until it receives a separate
vision provider/prompt review.

Manual analyzer boundary: `scripts.analyze_chart` writes a report/snapshot/chart and
does not send Telegram unless `--send-telegram` is passed. `scripts.run_latest_analysis`
is more execution-adjacent: it is interactive and can import `scripts.auto_execute` after
an ENTRY result only when `AUTO_TRADE` is enabled and
`RUN_LATEST_ANALYSIS_ALLOW_AUTO_EXECUTE=1` is also set. Neither file is the farm/PFR
paper runtime.

Current correction: the manual analyzer chart is explicitly documented in each snapshot
as a 15m execution chart with 5m trigger context and 1H/4H regime/veto context. The
legacy engine still computes entry/SL/TP geometry from 15m levels, so a 1H/4H-only
chart would be visually cleaner but less truthful.

## Machine-Checkable Invariant

`python -m scripts.strategy_lab.operational_health` exposes
`telegram_delivery_flow`, `llm_surface_boundaries`, and `product_analyzer_boundary`:

- `farm_core_sends_telegram = false`
- `paper_sends_telegram_by_default = false`
- `paper_sender_cli = scripts.strategy_lab.paper_telegram_sender`
- `paper_sender_chat_env = SUBSCRIPTION_USERS`
- `execution_authority = false`
- `telegram_analyzer_current_for_farm = false`
- `telegram_analyzer_imports_auto_execute = true`
- `telegram_analyzer_auto_trade_guarded = true`
- `telegram_analyzer_requires_auto_execute_opt_in = true`
- `llm_surface_boundaries.telegram_chart_formatter_provider = yandex_only`
- `llm_surface_boundaries.telegram_chart_formatter_status.schema = llm_formatter_provider.v1`
- `llm_surface_boundaries.telegram_chart_formatter_configured = true/false`
- `llm_surface_boundaries.telegram_chart_formatter_uses_llm_provider_env = false`
- `llm_surface_boundaries.telegram_chart_formatter_effective_provider_scope =
  shared_llm_client_opt_in` when the reviewed product launchers set
  `PRODUCT_ANALYZER_LLM_ROUTER=llm_client`
- `llm_surface_boundaries.telegram_chart_formatter_effective_shared_entrypoints =
  ["generate_client_text", "generate_edu_text"]` under that effective launcher route
- `llm_surface_boundaries.scanner_formatter_provider_mismatch = false` when the
  reviewed product launchers route text-only formatter calls through the same shared
  `LLM_PROVIDER` path as the scanner.
- With `PRODUCT_ANALYZER_LLM_ROUTER=llm_client`, the direct formatter status reports
  `provider_scope = shared_llm_client_opt_in` and
  `telegram_chart_formatter_uses_llm_provider_env = true`. In a bare shell the direct
  formatter may still report `telegram_chart_formatter_provider = yandex_only`; use
  `telegram_chart_formatter_effective_provider_scope` to evaluate the reviewed
  launcher route.
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

`product_analyzer_revival_checklist` is the high-level summary for the old manual
product analyzer path. For the current paper/research loop it should show:

- `canonical_paper_cycle_allowed = true`;
- `manual_product_alerts_allowed = false`;
- `live_execution_allowed = false`;
- `remaining_review` still includes premium vision, manual Telegram card review,
  rate-limit/dedup review, and a future executor contract.

That checklist is intentionally not a Telegram enable switch. It confirms that
farm/PFR can run while the old product/analyzer alert surface stays separated.

## Future Live Notification Rule

If real paper alerts are enabled, they must remain the separate opt-in sender process or
flag:

- read-only over derived farm/paper artifacts;
- active subscriber/superadmin bot chats only, never scanner/default public chats;
- rate-limited and deduped;
- no `.env` writes, no `AUTO_TRADE`, no private account endpoints, no order calls;
- one alert per state change, not one alert per loop tick.
