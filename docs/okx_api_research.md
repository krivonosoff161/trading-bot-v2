# OKX API Research For Signal Quality

Date: 2026-04-05

## Scope

Goal: identify which OKX API v5 endpoints can add signal value beyond the small subset currently used in [src/exchange/okx_client.py](C:/Users/krivo/trading-bot-v2/src/exchange/okx_client.py).

Important note:
- The current English OKX docs page clearly exposes Market Data, Public Data, and public WebSocket sections.
- The docs navigation/search still lists Trading Statistics / Rubik items, but the detailed Rubik REST sections are not fully surfaced in the current exported English HTML.
- For those Rubik rows below, I used:
  - the official OKX docs taxonomy,
  - live official OKX public endpoint responses on `www.okx.com`,
  - and the current repository usage.

## What the current client already uses

Used now in [src/exchange/okx_client.py](C:/Users/krivo/trading-bot-v2/src/exchange/okx_client.py):

| Endpoint | Purpose | Used now |
|---|---|---|
| `GET /api/v5/public/instruments` | instrument specs | Yes |
| `GET /api/v5/market/candles` | latest candles | Yes |
| `GET /api/v5/market/history-candles` | historical candles | Yes |
| `GET /api/v5/market/ticker` | last price / best bid-ask / 24h stats | Yes |
| `GET /api/v5/market/books` | top-of-book snapshot | Yes |
| `GET /api/v5/market/trades` | recent trades | Yes |
| `GET /api/v5/market/history-trades` | historical public trades | Yes |
| `GET /api/v5/public/open-interest` | current OI | Yes |
| `GET /api/v5/rubik/stat/contracts/open-interest-history` | historical OI | Yes |
| `GET /api/v5/public/funding-rate` | current funding | Yes |
| `GET /api/v5/public/funding-rate-history` | historical funding | Yes |
| `GET /api/v5/account/positions-history` | own closed positions | Yes |

Main gaps:
- no mark price / mark candles
- no index price / index candles
- no liquidation feed
- no taker-volume aggregate feed
- no long/short ratio feed
- no deeper book than current top levels
- no option volatility / vol-surface input

## Executive view

Best unused additions for signal quality:

- `P0` Mark price + mark-price candles
- `P0` Index candles / index-vs-last divergence
- `P0` Liquidation orders public WebSocket channel
- `P0` Rubik taker volume
- `P1` Contract-specific long/short account ratio
- `P1` Full order book / deeper depth snapshot
- `P2` Option summary / implied-vol regime for BTC and ETH only

What I would not prioritize first:

- block trade / RFQ-specific APIs
- options estimated delivery/exercise price for perps
- private account-only endpoints for alpha generation

## Endpoint inventory

### 1. Order book / market depth

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P1` | `GET /api/v5/market/books` | Public | `asks`, `bids`, `ts`, `seqId`; each level includes price, size, order count | Docs: updated once every 50 ms; `40 req / 2s` per IP | Yes | Good for top-of-book imbalance, spread, thin liquidity, spoof-like wall changes |
| `P1` | `GET /api/v5/market/books-full` | Public | fuller book snapshot with deeper `asks`/`bids`, `ts` | Docs: updated once a second; `10 req / 2s` per IP | No | Better than top-5 when you need persistent wall detection, depth concentration, absorption around entry/TP |
| `P1` | Public WS `books` / `books5` channels | Public WS | incremental or snapshot depth updates | Streaming | No | Better than REST for timing; useful if you want true order-flow timing instead of 15m snapshots |

### 2. Trades / tape / liquidations

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P1` | `GET /api/v5/market/trades` | Public | `instId`, `side`, `sz`, `px`, `tradeId`, `ts`, `source` | `100 req / 2s` per IP, up to `500` trades | Yes | Good for micro delta, aggression, burst detection, sweep size |
| `P1` | `GET /api/v5/market/history-trades` | Public | same trade fields, paginated up to 3 months | `20 req / 2s` per IP, max `100` per request | Yes | Useful for backfill/backtest of microstructure features |
| `P0` | Public WS `trades` / `trades-all` channels | Public WS | streaming prints with lower latency than REST | Event-driven | No | Strong timing improvement if you want actual tape-based trigger freshness |
| `P0` | Public WS `liquidation-orders` channel | Public WS | recent liquidation prints by instrument type; for futures/swaps max one order per contract per second | Streaming only in current docs; no simple REST liquidation endpoint surfaced | No | Strong for cascade detection, climax flushes, exhaustion after squeeze, avoid chasing after liquidation spike |

Notes:
- In current v5 docs, I did not find a simple public REST liquidation endpoint; the documented public path is the WebSocket channel.
- For signal timing, this is one of the clearest API upgrades from “slow snapshot” to “live event”.

### 3. Open interest

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P1` | `GET /api/v5/public/open-interest` | Public | `oi`, `oiCcy`, `oiUsd`, `ts`, `instId`, `instType` | `20 req / 2s` per IP + instrument ID | Yes | Current OI confirms whether move is opening participation vs empty price drift |
| `P1` | `GET /api/v5/rubik/stat/contracts/open-interest-history` | Public | live-verified arrays: `[ts, oi_contracts, oi_ccy, oi_usd]` | Docs section not surfaced in current EN export; live endpoint works | Yes | Strong for OI trend, squeeze build-up, OI divergence vs price |
| `P1` | Public WS `open-interest` channel | Public WS | current OI push | Docs: every 3 seconds when updated | No | Better than polling current OI if you want live confirmation during entry windows |

### 4. Funding rate

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P1` | `GET /api/v5/public/funding-rate` | Public | live-verified fields: `fundingRate`, `fundingTime`, `nextFundingTime`, `premium`, `minFundingRate`, `maxFundingRate`, `settState`, `formulaType`, `interestRate` | Live endpoint works; current EN docs export does not expose this REST section cleanly | Yes | Good for crowding, overheated positioning, funding interval awareness |
| `P1` | `GET /api/v5/public/funding-rate-history` | Public | historical `fundingRate`, `fundingTime`, `method`, bounds / state fields | `10 req / 2s` per IP + instrument ID; up to 3 months | Yes | Useful for funding percentile / z-score instead of raw funding |
| `P1` | Public WS `funding-rate` channel | Public WS | current funding payload with `fundingRate`, `premium`, `nextFundingTime`, `settState` | Docs: pushed every 30s to 90s | No | Better if funding/premium is used as a live filter near entry |

### 5. Long/short ratio

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P1` | `GET /api/v5/rubik/stat/contracts/long-short-account-ratio` | Public | live-verified arrays: `[ts, ratio]`; requires `ccy`, optional `instType` | Docs navigation lists Trading Statistics; detailed EN section not surfaced in current export | No | Good regime sentiment input: crowded long/short side by coin, slower than tape but useful as backdrop |
| `P1` | `GET /api/v5/rubik/stat/contracts/long-short-account-ratio-contract` | Public | live-verified arrays: `[ts, ratio]`; requires `instId` | Same docs visibility caveat as above | No | More precise than coin-level ratio; better for pair-specific crowding |

Notes:
- I did not get a valid live response for a current `long-short-position-ratio` endpoint; current guesses returned `404`.
- I would not wire a position-ratio endpoint until it is re-verified in current docs/live API.

### 6. Taker buy/sell volume

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P0` | `GET /api/v5/rubik/stat/taker-volume` | Public | live-verified time series arrays with `ts` plus two directional taker-volume columns; works with `ccy` and `instType=CONTRACTS` | Docs navigation lists Trading Statistics; detailed EN section not surfaced in current export | No | Very valuable higher-timeframe confirmation of aggressive buying vs selling; stronger and less noisy than 100-trade micro delta alone |

Notes:
- This is one of the most attractive missing feeds because it bridges your current micro delta and your slower 15m/1H regime logic.
- Exact field order should be rechecked against the live docs section before coding because the current EN HTML export does not show the response table.

### 7. Index / mark price

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P0` | `GET /api/v5/public/mark-price` | Public | `markPx`, `instId`, `instType`, `ts` | `10 req / 2s` per IP + instrument ID | No | Critical for derivatives: separates fair value from last traded price and protects against wick/manipulation noise |
| `P0` | `GET /api/v5/market/mark-price-candles` | Public | mark-price OHLC candles | `20 req / 2s` per IP | No | Lets you build trend/ATR/VWAP-like logic on fair price instead of last price |
| `P1` | `GET /api/v5/market/history-mark-price-candles` | Public | historical mark-price candles | `20 req / 2s` per IP | No | Necessary if you want backtest parity for mark-price features |
| `P0` | `GET /api/v5/market/index-candles` | Public | index-price OHLC candles | `20 req / 2s` per IP | No | Strong for basis/premium features: compare perp last vs spot index trend |
| `P1` | `GET /api/v5/market/history-index-candles` | Public | historical index candles | `10 req / 2s` per IP | No | Useful for backtests of basis divergence and index-led direction |
| `P1` | Public WS `mark-price` channel | Public WS | mark price stream | Docs: every 200 ms on change, every 10s otherwise | No | Best live basis/stress input if you later add live execution timing |

Why this matters:
- Your current signals are based mostly on traded price candles.
- For derivatives, mark price and index price often explain whether move quality is real or only perp-specific dislocation.

### 8. Large orders / block trades

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P2` | No simple public market-data REST block-trade endpoint surfaced in current v5 English docs | — | — | — | No | Not a first-step source for this bot |
| `P2` | Private account bills / history can show block-trade subtypes `204-209` | Private | only your own block-trade fills / bill records | account-scoped | No | Not usable as public signal alpha; only useful for own execution audit |

Recommendation:
- Do not spend time on block-trade APIs first.
- If the goal is “large orders”, deeper public book + public trades + liquidation feed are much higher ROI.

### 9. Options / volatility / options-derived regime data

| Priority | Endpoint URL | Auth | What it returns | Update / limits | Used now | Signal value |
|---|---|---|---|---|---|---|
| `P2` | `GET /api/v5/public/opt-summary` | Public | option surface summary: `markVol`, `bidVol`, `askVol`, `realVol`, `volLv`, `delta`, `gamma`, `vega`, `theta`, `fwdPx`, `ts` | `20 req / 2s` per IP + instFamily | No | Useful as BTC/ETH macro volatility regime input; weak direct value for XRP/DOGE/SOL intraday entries |
| `P2` | `GET /api/v5/public/estimated-price` | Public | estimated delivery / exercise price near expiry | `10 req / 2s` per IP + instrument ID | No | Mostly irrelevant for perpetual intraday bot |
| `P2` | Public WS `opt-summary` | Public WS | streaming option surface / vol summary | Streaming | No | Only worth it if you explicitly add options-implied vol regime logic |

## Priority summary

### P0: highest value

1. `GET /api/v5/public/mark-price`
2. `GET /api/v5/market/mark-price-candles` + `history-mark-price-candles`
3. `GET /api/v5/market/index-candles` + `history-index-candles`
4. Public WS `liquidation-orders`
5. `GET /api/v5/rubik/stat/taker-volume`

Why:
- These add information your current client does not already approximate.
- They improve either fairness of price (`mark/index`) or directional participation (`taker volume`, liquidations).

### P1: useful next layer

1. `GET /api/v5/market/books-full`
2. Public WS `open-interest`
3. Public WS `funding-rate`
4. `GET /api/v5/rubik/stat/contracts/long-short-account-ratio`
5. `GET /api/v5/rubik/stat/contracts/long-short-account-ratio-contract`

Why:
- Strong supportive filters and regime context, but less direct than `mark/index/liquidations/taker-volume`.

### P2: interesting, not urgent

1. `GET /api/v5/public/opt-summary`
2. `GET /api/v5/public/estimated-price`
3. block-trade / RFQ-related account endpoints

Why:
- Mostly macro / niche / execution-audit value, not immediate intraday perp signal edge.

## Concrete gap analysis vs current client

### Already covered well enough

- current last price
- latest candles and historical candles
- top-of-book snapshot
- recent and historical public trades
- current OI and OI history
- current funding and funding history

### Clear missing alpha candidates

1. **Mark-vs-last divergence**
   - Add `markPx` and mark-price candles.
   - Derived features:
     - `basis_pct = (last - mark) / mark`
     - mark-price trend vs traded-price trend
   - Value:
     - catch stretched perp moves
     - reduce false breakout signals caused by perp premium/discount noise

2. **Index-vs-perp divergence**
   - Add index candles and compare index move vs perp move.
   - Derived features:
     - perp premium expansion
     - perp move without index confirmation
   - Value:
     - especially useful for BTC and ETH
     - can downgrade “perp-only squeeze” signals

3. **Aggregate taker flow**
   - Add Rubik taker-volume.
   - Derived features:
     - higher-timeframe aggressive buy/sell imbalance
     - taker flow acceleration before signal
   - Value:
     - cleaner than only using the latest 100 trades
     - better regime confirmation for DRIFT/TRENDING

4. **Liquidation bursts**
   - Add liquidation-orders public WebSocket.
   - Derived features:
     - liquidation burst in trade direction
     - exhaustion after one-sided liquidation spike
   - Value:
     - may explain late-entry failures and climax candles

5. **Crowding / positioning backdrop**
   - Add long/short account ratio.
   - Derived features:
     - crowded longs or crowded shorts
     - signal in same direction as crowded side vs against it
   - Value:
     - better as regime/risk modifier than raw entry trigger

## Recommended implementation order

If the next step is practical signal improvement, not research theater:

1. `mark-price` + `mark-price-candles`
2. `index-candles`
3. `rubik/stat/taker-volume`
4. public WS `liquidation-orders`
5. `books-full` or WS order book only after the above

Reason:
- these give the biggest new information gain relative to what the bot already computes
- they are easier to translate into concrete features than options data or block-trade flows

## Sources

- OKX API v5 docs root: https://app.okx.com/docs-v5/en/
- Order book: https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-order-book
- Full order book: https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-full-order-book
- Trades: https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-trades
- Trades history: https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-trades-history
- Open interest: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-open-interest
- Funding rate history: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history
- Mark price: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-mark-price
- Index candles: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-index-candlesticks
- Index candles history: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-index-candlesticks-history
- Mark price candles: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-mark-price-candlesticks
- Mark price candles history: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-mark-price-candlesticks-history
- Option market data: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-option-market-data
- Estimated delivery/exercise price: https://app.okx.com/docs-v5/en/#public-data-rest-api-get-estimated-delivery-exercise-price
- Open interest WS: https://app.okx.com/docs-v5/en/#public-data-websocket-open-interest-channel
- Funding rate WS: https://app.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel
- Mark price WS: https://app.okx.com/docs-v5/en/#public-data-websocket-mark-price-channel
- Liquidation orders WS: https://app.okx.com/docs-v5/en/#public-data-websocket-liquidation-orders-channel

Rubik note:
- The current English docs page still lists Trading Statistics / Rubik in search/navigation, but detailed Rubik REST sections were not fully surfaced in the current exported HTML I inspected on 2026-04-05.
- For `open-interest-history`, `taker-volume`, and long/short ratio rows above, existence and response shape were additionally verified against live official OKX public endpoints on `https://www.okx.com`.
