# Theme 40 / Microstructure Lane — Stage 1 audit (2026-06-21)

A SEPARATE research-only lane (orderbook pressure / moving liquidity wall), NOT an OHLCV strategy
family, NOT a replacement for farm_loop. Public market data only. No edge claims, no paper-ready.

## What already exists / reusable / must-not-touch

| asset | what it is | disposition |
|---|---|---|
| `signal_engine._build_micro_snapshot(book, trades)` | pure math: OBI top-5, spread_bps, trade_delta_100, buy/sell vol & count from books5 + trades | **REUSE as pure math** (port + tests; do not import the live engine) |
| `src/data/okx_trade_stream.py` (`OKXTradeStream`) | **keyless public WS trades** (`wss://ws.okx.com/ws/v5/public`, channel `trades`), reconnect+backoff, stop_event | **REUSE as transport reference** (trades only; no books channel) |
| `okx_client.get_books` / `get_trades` | endpoints `/api/v5/market/books`, `/api/v5/market/trades` are **public**, but the client carries API keys (`OK-ACCESS-*`, `_sign`) | **DO NOT IMPORT** (live, key-bearing = path to money). Build a keyless public fetcher instead |
| `scripts/ws/ws_scanner.py` | live pump scanner; fetches books5 via the **authed** client | reference only; do not import |
| old pump docs (`docs/*pump*`, `ws_truth_report`, `ws_pattern_mining_report`) | the pump-prediction engine, closed (retail can't beat extraction bots) | Theme 40 is microstructure EXECUTION, not pump prediction — different question, not a reanimation |

## Data inventory — the decisive fact

| data | status |
|---|---|
| **Trade tape (ticks)** | **EXISTS** — `manifests/tape_files_*.csv` indexes **632 files, 55 symbols, 20 days (2026-05-11→05-31), 115.5M ticks** at `E:\trading-data\ticks\{SYMBOL}\{date}.csv[.gz]`. Schema `ts_ms,recv_ts_ms,symbol,side,price,size,trade_id` — **trades-only, with aggressor side**. All 632 abs_paths present. |
| **Orderbook (book levels / walls)** | **DOES NOT EXIST** — no historical book snapshots. Walls / imbalance / spoof / wall-persistence CANNOT be backtested; they need FORWARD collection. |

## What this means for the lane (honest split)

- **Trades sub-lane (has data):** aggressive buy/sell pressure, CVD/trade-delta, tape speed, and mechanical
  follow-through after a pressure event are **replayable on the 115M-tick real tape NOW** — no waiting,
  no fake backtest. (`micro_tape_replay`.)
- **Orderbook sub-lane (no data):** walls / imbalance / spoof require a **forward recorder** (keyless
  public `books` + `trades` WS). Build the bounded collector + a data-readiness report; features are
  defined but their replay is blocked until enough is collected. (`micro_recorder`.)

## What is NOT done (so we don't pretend)

No orderbook history → no wall/imbalance/spoof edge can be measured today; the recorder must run first.
The trades-side follow-through is observable now, but "follow-through observed" != edge.
