# Telegram / LLM Notification Revival - 2026-06-28

Status: **product notification layer restored for guarded testing**.

This pass covers Telegram delivery ownership, subscriber/public routing, message audit,
old runtime-log archival, and real text-provider A/B checks. It does not enable live
trading, `AUTO_TRADE`, private OKX endpoints, or order execution.

## Routing Contract

| Event class | Destination | Subscription required | Public signal levels |
|---|---|---:|---:|
| `NEWS`, `MARKET_SUMMARY`, `SCANNER_WATCH`, `SCANNER_GO_PUBLIC_TEASER` | notification channel | no | no |
| `ACTIONABLE_ANALYSIS`, `GO`, `WATCH`, `PAPER_SETUP`, `VIP`, `EDUCATION` | personal bot chat | yes | no for `PAPER_SETUP` |
| `ADMIN_DIAGNOSTIC`, `FARM_ERROR`, `PAPER_DEBUG`, `DELIVERY_ERROR` | admin chat | admin only | no |

`PAPER_SETUP` is subscriber-only by policy. It may contain calculated entry/stop/take
context and must not be mirrored to a public channel as a full setup.

## New Components

| File | Role |
|---|---|
| `src/utils/notification_policy.py` | Pure event policy: decides destination and access requirements. |
| `src/utils/telegram_delivery_router.py` | Dry-run by default; sends only when explicitly requested. |
| `src/utils/telegram_audit.py` | Append-only JSONL audit for inbound/outbound Telegram events. |
| `scripts/telegram_delivery_smoke.py` | CLI smoke for public/subscriber/admin routes. |
| `scripts/llm_provider_ab.py` | Real Yandex vs Alibaba text-provider A/B check. |
| `scripts/archive_runtime_logs.py` | Moves old runtime logs to `logs_archive/<label>/` with a manifest. |

## Audit Log

Telegram message audit path:

```text
logs/telegram/message_audit.jsonl
```

Each row contains a timestamp, chat id, direction, mode, event, delivery status,
message id when Telegram returns one, text hash, and a short preview. It intentionally
does not store tokens or provider secrets.

The legacy product bot now records:

- incoming text;
- incoming callback clicks;
- incoming photo uploads;
- outgoing normal bot messages;
- outgoing manual-analysis summaries;
- outgoing chart photos.

Product decision-event audit path:

```text
logs/signals/signal_events.jsonl
```

This is the analysis/training-oriented log. Manual pair analysis writes one
`signal_event.v1` row after the report/snapshot/chart/summary are produced. VIP
screenshot analysis writes one row after the vision provider returns a result or a
provider failure. These rows store normalized decision fields and artifact references,
not raw secrets or execution authority:

- source/mode/decision;
- symbol/timeframe/side when available;
- entry/stop/TP/max-hold/risk fields when the deterministic analyzer produced them;
- provider/model/prompt version when an LLM was involved;
- local artifact refs for the snapshot, report, chart, summary, premium screenshot,
  and premium log;
- `paper_only=true`;
- `execution_allowed=false`.

This closes the gap where Telegram/VIP activity was visible as chat traffic but not
as a machine-readable decision package for later outcome analysis.

## Runtime Log Archive

Old runtime logs were archived before the new notification tests:

```text
logs_archive/revival_2026-06-28_telegram_llm_prelaunch/
```

The archive command skips subscription state and does not touch `.env`:

```powershell
python scripts/archive_runtime_logs.py --label revival_2026-06-28_telegram_llm_prelaunch --apply
```

## Provider A/B Result

Command:

```powershell
python scripts/llm_provider_ab.py --providers yandex,alibaba --max-tokens 500 --apply
```

Observed on 2026-06-28:

| Case | Yandex | Alibaba | Current interpretation |
|---|---:|---:|---|
| education leverage | 14.2s, ~0.292 RUB | 3.4s, ~0.055 RUB | Alibaba is cheaper/faster and good enough for educational text. |
| manual `NO_TRADE` | 1.5s, ~0.075 RUB | 14.4s, ~0.104 RUB | Yandex was shorter and better disciplined for no-trade formatting. |
| scanner `WATCH` | 7.3s, ~0.182 RUB | 27.7s, ~0.156 RUB | Both preserved the non-signal boundary; Alibaba was verbose and slow here. |

Working verdict for the next pass:

- education/VIP text can use Alibaba when cost/speed matter;
- manual trading cards and scanner cards should stay under stricter prompt tests before
  switching wholesale;
- provider choice must stay explicit and auditable, not hidden behind Telegram delivery.

Raw provider responses are local runtime artifacts under `logs/llm_provider_ab/` and are
not committed.

## 2026-06-28 Runtime Follow-up

Operator testing showed two product-layer issues:

1. Manual pair analysis was slow because the chart analyzer spent an LLM formatter call
   even for deterministic `NO_TRADE` results. This is now changed: `NO_TRADE` uses the
   engine summary by default, while `ENTRY`/`WAIT` cards still use the formatter.
   Operators can opt back into LLM-formatted no-trade cards with:

   ```powershell
   $env:PRODUCT_ANALYZER_LLM_FOR_NO_TRADE = "1"
   ```

2. Premium screenshot analysis failed with Yandex/Gemma `HTTP 403`. This is a provider
   authorization/configuration failure on the vision-only path. The shared Alibaba text
   router does not handle images, so this was not fixed by text-provider routing. The bot
   now reports a clear temporary vision-provider failure instead of a generic retry
   message.

Neither change enables order execution, `AUTO_TRADE`, private OKX endpoints, or automatic
Telegram trade alerts.

## 2026-06-28 Subscriber Paper Delivery Update

Paper setup alerts are now routed as a subscriber product surface, not as a public
channel broadcast:

- `scripts.strategy_lab.paper_telegram_sender` reads validated
  `paper_telegram_preview` artifacts and sends only to active Telegram bot
  subscribers/superadmins from `scripts/subscriptions.json`.
- Raw chat IDs are not written into the paper delivery artifact; delivery rows store
  a recipient hash plus status.
- Delivery is deduplicated per `preview_id` and recipient hash via
  `state/derived/paper_telegram_sent_keys.json`, so a sender loop cannot spam the
  same setup repeatedly.
- `farm_loop` remains Telegram-decoupled. It only builds preview and dry-run delivery
  audit artifacts. Real network sends run through the separate visible
  `bat/strategy_lab_paper_telegram_sender_loop.bat` surface.
- `bat/strategy_lab_control_room.bat` starts the sender window only when
  `STRATEGY_LAB_PAPER_TELEGRAM_SEND=1` is explicitly set.

Runtime smoke after the change:

| Check | Result |
|---|---|
| Paper sender dry-run | `configured=true`, `targets=4`, `eligible=3`, `sends_network=false` |
| Operational health | `paper_telegram_surface=pass`, delivery target `active_subscription_users` |
| Tests | `79 passed`, ruff clean on touched files |

VIP screenshot analysis is still blocked by provider authorization:

| Surface | Result |
|---|---|
| Text LLM A/B | Yandex and Alibaba both returned text successfully. |
| Premium screenshot vision | Yandex/Gemma returned `HTTP 403 Forbidden` with key/model URI present. |

Interpretation: text LLM routing works; VIP screenshot failure is a Yandex/Gemma vision
access/configuration issue, not a Telegram delivery failure. The bot reports this as a
temporary vision-provider failure instead of pretending the screenshot was analyzed.

## 2026-06-28 Product/Farm Debt Closure

This follow-up closed four operator-facing debts without changing trade authority:

1. Paper setup Telegram previews now render as human-readable cards: setup family,
   entry, stop, targets, max hold, source/verdict provenance, and the required
   `research-only, not an order` / `execution_allowed=false` boundary.
2. The Telegram superadmin panel has a read-only farm status button. It reads the
   farm cockpit snapshot and cannot start/stop loops, send alerts, change `.env`, or
   execute orders.
3. Manual chart analysis now writes an explicit `chart_plan` into the snapshot:
   15m execution chart, 5m trigger context, 1H/4H regime/veto context. This documents
   the current main-engine reality instead of pretending every analysis is a single-TF
   chart.
4. Premium screenshot vision remains blocked by the Yandex/Gemma authorization/config
   issue. Alibaba is currently integrated for text-only paths; adding Alibaba vision
   would require a separate image-capable adapter and prompt review, not a silent
   fallback.
5. Manual and VIP product events are now captured in
   `logs/signals/signal_events.jsonl`, with health checks for schema, paper-only
   status, and execution-disabled rows.

## Telegram Smoke Result

Dry-run checks:

```powershell
python scripts/telegram_delivery_smoke.py --event-type MARKET_SUMMARY --text "[TEST] Dry-run public route. Not a trading signal."
python scripts/telegram_delivery_smoke.py --event-type PAPER_SETUP --use-subscribers --text "[TEST] Dry-run subscriber paper setup route. Not a trading signal."
```

Real smoke sends were then executed with explicit test labels:

```powershell
python scripts/telegram_delivery_smoke.py --event-type MARKET_SUMMARY --chat-env SCANNER_CHAT_ID --symbol SYSTEM --text "[TEST] Strategy Bot notification channel route check. Not a trading signal." --send
python scripts/telegram_delivery_smoke.py --event-type PAPER_SETUP --use-subscribers --symbol SYSTEM --text "[TEST] Проверка маршрута подписочных paper setup карточек. Это не торговый сигнал." --send
```

Observed result:

- public channel route delivered 1 message to the existing scanner notification channel;
- subscriber `PAPER_SETUP` route delivered 3 messages to active/superadmin subscriber
  chats;
- all deliveries were written to `logs/telegram/message_audit.jsonl`.

`TELEGRAM_NOTIFICATION_CHAT_ID` was not configured at the time of the smoke, so the
public test used the existing `SCANNER_CHAT_ID`. For production naming clarity, set
`TELEGRAM_NOTIFICATION_CHAT_ID` later and leave `SCANNER_CHAT_ID` as a compatibility
fallback only when explicitly requested.

## Safety Boundary

- No `.env` edits.
- No `AUTO_TRADE`.
- No order execution.
- No private OKX/account endpoint usage.
- Telegram is a delivery surface only.
- LLM providers format already-computed text; they do not mint trades or alter levels.

## Remaining Work

1. Wire scanner/farm events into the policy router instead of calling Telegram directly
   from each subsystem.
2. Set a canonical `TELEGRAM_NOTIFICATION_CHAT_ID` and keep legacy `SCANNER_CHAT_ID`
   as a documented fallback.
3. Review VIP screenshot and education prompts separately; this pass only proved shared
   routing and text-provider A/B for representative text cases.
4. Add rate limits/dedup for automatic subscriber notifications before unattended sends.
5. Keep old main execution code isolated until a separate paper-first executor contract
   is reviewed and tested.
