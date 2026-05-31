# Scout #1 source candidates — keyless/free shortlist (31.05.2026)

Задача: из `cporter202/API-mega-list` отобрать 10-15 крипто/финанс-релевантных источников для будущего Scout #1. Это **не интеграция** и не live-проверка эндпоинтов; финальную проверку доступности/парсинга делает Claude перед кодом.

Важное ограничение: `API-mega-list` сейчас в основном каталог Apify-акторов. Apify-акторы не берем как зависимость по умолчанию: для запуска обычно нужен Apify runtime/API token и часто pay-per-result. В таблице ниже API-mega-list использован как навигация к upstream-источникам; кандидат = прямой источник, не чужой scraper-код.

Уже есть и не дублируем: CoinGecko global/markets/trending, RSS Cointelegraph/Decrypt/CoinDesk/BitcoinMagazine/CryptoSlate, Fear&Greed alternative.me, OKX public, DeFiLlama stablecoins/TVL/DEX, mempool.space, blockchain.info.

## Топ-5 брать первыми

1. **Google News RSS** — самый дешевый прирост к Scout #1: keyword news по `bitcoin OR crypto OR solana` без ключей, сразу `title + source + pubDate`.
2. **DexScreener API** — не дублирует OKX/CoinGecko: DEX/new-pair/liquidity слой по Solana/Base/ETH, потенциально ранний инфо-сигнал.
3. **Polymarket Gamma API** — публичные prediction-market события по crypto/regulation; дает структурные цены/объемы/время.
4. **Hacker News Firebase API** — keyless tech/dev attention; полезен для инфраструктурных крипто-новостей, эксплойтов, regulatory-tech тем.
5. **GitHub REST API** — релизы/коммиты/звезды crypto-инфра репо; хорошо логируется как событийный сигнал до/после market reaction.

## Таблица кандидатов

| name | url | категория в API-mega-list / pointer | keyless? | free tier? | формат | релевантность | rate-limit | OOS-логировать? |
|---|---|---|---|---|---|---|---|---|
| Google News RSS search | `https://news.google.com/rss/search?q=bitcoin%20OR%20crypto%20OR%20solana&hl=en-US&gl=US&ceid=US:en` | `News`: много `Google News Scraper` | Да, RSS без ключа | Да | RSS/Atom XML | high | Не проверено; нужен вежливый polling, например 5-15 мин | Да: `title`, `source`, `pubDate`, `link` |
| Google Trends RSS / Trending now | `https://trends.google.com/trends/trendingsearches/daily/rss?geo=US` | `News/Social`: `Google Trends Scraper` | Да, RSS endpoint заявлен в Google Trends UI/help | Да | RSS XML | med | Не проверено; Google может менять/ограничивать | Да: query/title + publish time; crypto-фильтр по ключевым словам |
| Hacker News Firebase API | `https://hacker-news.firebaseio.com/v0/` | `News/Open Source`: `Hacker News Data Scraper`, `Hacker News Intelligence` | Да | Да | JSON | med | В официальном HN API указано "currently no rate limit"; все равно throttling | Да: item id, `time`, `title`, `score`, `url`, comments |
| Reddit public RSS/JSON | `https://www.reddit.com/r/CryptoCurrency/new.rss` / `https://www.reddit.com/r/CryptoCurrency/new.json` | `Social/MCP`: `Reddit Searcher`, `SubReddit Scraper`, `MCP Reddit` | Частично: public RSS/JSON без OAuth заявлен, но доступ нестабилен | Да | RSS/JSON | high | Нестабильно; агрессивные anonymous limits/403 возможны, нужен fallback RSS и низкая частота | Да: post title, created time, score/comments если доступно |
| Telegram public channel page | `https://t.me/s/<channel>` | `Social/Open Source`: `Telegram Channel Scraper`, `Telegram Message` | Да для публичных каналов через web page; не официальный API | Да | HTML | high | Неизвестно; хрупко, может ловить антибот | Да: message time + text/link, если parser устойчив |
| YouTube channel RSS | `https://www.youtube.com/feeds/videos.xml?channel_id=<CHANNEL_ID>` | `Social/News`: `YouTube Channel Intelligence`, `YouTube Channel Scraper` | Да | Да | Atom XML | med | Не проверено; feed обычно небольшой, polling 15-60 мин | Да: video id, title, published/updated, channel |
| GitHub REST API search/releases | `https://api.github.com/search/repositories?q=topic:cryptocurrency` / repo releases | `Open Source`: `GitHub Repository Intelligence`, `GitHub Repository Scraper` | Да для public data без token | Да | JSON | med | 60 req/hour unauthenticated + secondary limits | Да: repo/release event time, stars, topics, release tag |
| DexScreener API | `https://api.dexscreener.com/latest/dex/search?q=SOL` / docs | `Automation`: `Dexscreener Api`, `Dexscreenener Token Fetcher` | Да, public docs/API | Да, public/free endpoints | JSON | high | В docs есть endpoint-specific limits, например 60 или 300 requests/min | Да: pair created time, liquidity, volume, price change, chain |
| Polymarket Gamma API | `https://gamma-api.polymarket.com/events` | `News/Automation`: `Polymarket Markets Scraper`, `Polymarket Scraper` | Да: Gamma/Data public без auth по docs | Да | JSON | high | Не проверено; сторонние справки указывают около 60 rpm, официально уточнить | Да: market/event id, active/closed, volume, prices, timestamps |
| Kalshi public market data | `https://api.elections.kalshi.com/trade-api/v2/markets` | `News/Automation`: `Kalshi Scraper` | Вероятно да для public market data; trading/private требуют auth | Да для public market data | JSON | med | Не проверено; rate limit в docs/help надо уточнить перед кодом | Да: market ticker, close/expiry time, yes/no prices, volume |
| Binance public market data | `https://api.binance.com/api/v3/ticker/24hr` | `Automation`: `Unified Crypto Orderbook Scraper`, Binance leaderboard/orderbook items | Да для market data endpoints | Да | JSON/WebSocket | med | Request-weight model; `exchangeInfo` содержит rateLimits, default weight budget указан в docs | Да: external exchange volume/price/volatility timestamps |
| Coinbase Exchange Market Data API | `https://api.exchange.coinbase.com/products/BTC-USD/ticker` | `Automation`: `Unified Crypto Orderbook Scraper` mentions Coinbase | Да: market data public | Да | JSON/WebSocket | med | Public endpoints: 3 req/s, burst 6 req/s по Coinbase FAQ | Да: ticker/orderbook/trades with exchange timestamp |
| Kraken public REST market data | `https://api.kraken.com/0/public/Ticker?pair=XBTUSD` | `Automation`: `Unified Crypto Orderbook Scraper` mentions Kraken | Да: public endpoints без auth | Да | JSON/WebSocket | med | Kraken support: public endpoints <= 1 request/sec считается безопасно | Да: ticker/OHLC/trades with time |
| KuCoin public market data | `https://api.kucoin.com/api/v1/market/allTickers` | `News/Automation`: `Crypto Data Scraper!`, `Unified Crypto Orderbook Scraper` | Да для public market data | Да | JSON/WebSocket | med | Public unauthenticated endpoints counted by IP; VIP0 public quota указан в docs | Да: ticker/volume/change per symbol |
| Bybit V5 public market data | `https://api.bybit.com/v5/market/tickers?category=linear` | `Automation`: `Unified Crypto Orderbook Scraper` mentions Bybit | Да для market endpoints | Да | JSON/WebSocket | med | HTTP IP limit 600 requests / 5 sec; per-endpoint limits в docs | Да: external futures ticker/open interest context with time |

## Что не брать из найденного сейчас

- **Apify crypto/news/social actors как runtime** — даже если actor пишет "no login" или "no official API key", сам Apify-вызов обычно требует Apify token/account и может быть pay-per-result. Это против дефолта keyless/без денег.
- **Cointelegraph/Decrypt/CoinDesk/BitcoinMagazine/CryptoSlate scrapers** — источники уже есть как RSS в Scout #1.
- **CoinGecko actors** — CoinGecko уже есть напрямую.
- **DeFiLlama actors** — DeFiLlama уже есть в `research_scout_orchestrator.py` / `onchain_whales_probe.py`.
- **Coinglass/liquidation/Whale Watcher/CryptoPanic-like actors** — либо платно/ключи, либо уже честно отмечено как blocked/paid в on-chain probe.
- **Lead-gen/email/phone/social profile scrapers** — не наш слой и высокий compliance/noise risk.

## Практический порядок добавления

1. Сначала добавить только **Google News RSS** и **DexScreener API** в ручной live-check: оба дают разные типы сигнала и не требуют аккаунтов.
2. Потом **Polymarket Gamma API** как отдельный prediction-market блок, без trading/CLOB auth.
3. Затем **HN + GitHub** как tech/dev-attention блок.
4. Социальные источники (**Reddit/Telegram/YouTube**) добавлять последними: они шумнее и чаще ломаются.
5. Биржевые источники кроме OKX брать только если появится конкретная OOS-гипотеза "external liquidity/volume leads OKX universe"; иначе это дублирование цены.

## Источники для проверки

- API-mega-list локально просмотренные категории: `news-apis-590`, `mcp-servers-apis-131`, `open-source-apis-768`, `social-media-apis-3268`, `automation-apis-4825`, `ai-apis-1208`.
- DexScreener API docs: https://docs.dexscreener.com/api/reference
- Polymarket API docs: https://docs.polymarket.com/api-reference
- Hacker News official API: https://github.com/HackerNews/API
- GitHub REST rate limits: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- Coinbase Exchange docs: https://docs.cdp.coinbase.com/exchange/introduction/welcome
- Kraken public API rate limits: https://support.kraken.com/articles/206548367-what-are-the-api-rate-limits-
- KuCoin API rate limits: https://www.kucoin.com/docs-new/rate-limit
- Bybit V5 rate limits: https://bybit-exchange.github.io/docs/v5/rate-limit
- Binance Spot API docs: https://github.com/binance/binance-spot-api-docs
- Google Trends help: https://support.google.com/trends/answer/3076011
