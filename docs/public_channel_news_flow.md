# Public Channel News Flow

This document describes the public news-channel surface for the Telegram channel.
It is separate from the trading scanner and from paper/live execution.

## Why this exists

The trading scanner answers a strict question:

```text
Does this event create a bounded trading edge?
```

The public channel answers a different question:

```text
Is this useful market context for a public audience?
```

Those two questions must not share the same output gate. A `NO_GO` trade event can
still be a useful public news item, and a useful public news item must not become
a trade instruction.

## Architecture

```text
source_registry.yaml
  -> existing public adapters
     RSS, Telegram public web, OKX public, SEC, EIA/OPEC, Dexscreener
  -> src.scout.public_channel.collector
  -> bounded article extraction
  -> PublicNewsMachineDoc.v1 inside PublicChannelItem.raw
  -> PublicChannelItem.v1
  -> src.scout.public_channel.editor
     LLM editor or deterministic fallback
  -> PublicChannelPost.v1
  -> forbidden-advice validator
  -> src.utils.telegram_delivery_router
  -> NEWS / MARKET_SUMMARY public channel events
```

## Source layers

The channel reuses the existing source map:

- `src/scout/config/source_registry.yaml` tells which sources exist and whether
  they are enabled.
- `src/scout/config/layer_source_matrix.yaml` tells which market layer each
  source feeds.

The current live source families are:

- L1: BTC/ETH majors, crypto regime, macro crypto context.
- L2: alts, listings, DEX/on-chain, liquidation context.
- L3: metals and macro-precious context.
- L4: oil, gas and energy context.
- L5: equities, AI proxies, filings and earnings.
- L6: cross-layer watch, planned/context only.

## Public editor contract

The editor prompt is in `src/scout/public_channel/editor.py`.

Output shape:

```json
{
  "headline": "short public title",
  "category": "event type",
  "what_happened": "what happened",
  "why_matters": "why this may matter",
  "watch_points": ["what to watch"],
  "public_ok": true,
  "skip_reason": ""
}
```

The LLM editor receives a machine-readable news document: source, title, URL,
source class, layer, extraction status, text quality, and extracted article text
when available. It must write ready Russian prose, not copy the field labels into
the body. The formatter owns the visual Telegram structure.

Forbidden public output:

- direct buy/sell language;
- entry, stop, take-profit, leverage;
- profit guarantees;
- claims that the post is a trading signal.

Allowed public output:

- what happened;
- why the event matters;
- what to watch next;
- original source link.

## Commands

Dry-run news pass:

```bash
python scripts/public_channel_publisher.py --mode news --limit 2
```

Dry-run with the configured LLM editor:

```bash
python scripts/public_channel_publisher.py --mode news --limit 2 --use-llm
```

Actually send public news posts:

```bash
python scripts/public_channel_publisher.py --mode news --limit 2 --use-llm --send
```

Queue-only source collection:

```bash
python scripts/public_channel_publisher.py --mode collect
```

Publish from the existing queue without collecting sources first:

```bash
python scripts/public_channel_publisher.py --mode publish --limit 1 --use-llm --send
```

By default the public publisher routes to `SCANNER_CHAT_ID`, because this is the
current public channel surface in this project. Override with `--chat-env` if the
channel is moved to another environment variable.

Send a public aggregate paper-bot stats slice:

```bash
python scripts/public_channel_publisher.py --mode stats --send
```

Visible Windows loop:

```bat
bat\public_news_loop.bat
```

The loop collects public source items every 5 minutes and publishes one queued
item every 15 minutes. The queue lives under ignored runtime state:

```text
logs/scout/public_channel/news_queue.json
```

This split prevents a source lull from creating empty posting cycles. It also
keeps Telegram cadence separate from source scanning cadence. The loop uses
`--use-llm` for the publish step. If the provider is unavailable, the publisher
falls back to deterministic public posts and records the usage/status in the
audit log.

## Safety boundary

This surface:

- does not touch `.env`;
- does not enable `AUTO_TRADE`;
- does not place orders;
- does not read raw private strategy calculations for posts;
- uses only public source facts and aggregate paper-bot statistics;
- writes runtime audit data under ignored `logs/scout/public_channel/`.
