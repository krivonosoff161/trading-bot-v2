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

