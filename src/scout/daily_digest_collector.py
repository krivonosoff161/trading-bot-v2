# -*- coding: utf-8 -*-
"""
daily_digest_collector.py — SCOUT #1: keyless сборщик крипто-инфополя.

Детерминированный data-puller (БЕЗ LLM внутри). Тянет ТОЛЬКО keyless/бесплатные
публичные источники (CoinGecko public API, крипто-RSS, OKX public, Fear&Greed),
складывает всё в ОДИН структурированный бандл (JSON + человекочитаемый MD).
LLM-шаг (сводка из бандла) делает оператор отдельно.

Read-only. Ничего не торгует. Не трогает .env / ключи / auth трейдера / прод-движок.
Idempotent: каждый запуск перезаписывает bundle_latest.{json,md}.

Запуск:
    python scripts/analysis/research/daily_digest_collector.py
"""

import sys
import io
import os
import json
import time
import datetime as dt
import xml.etree.ElementTree as ET

import requests

# Windows консоль — пишем UTF-8 (как в остальных research-скриптах проекта).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Хранилище данных новой системы: logs/scout/ (src/scout/ -> ../.. = корень проекта).
_SCOUT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
OUT_DIR = os.path.join(_SCOUT_ROOT, "logs", "scout")
BUNDLE_JSON = os.path.join(OUT_DIR, "bundle_latest.json")
BUNDLE_MD = os.path.join(OUT_DIR, "bundle_latest.md")

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 research-scout; keyless)"}
TIMEOUT = 20

# Вежливый троттлинг между запросами к одному хосту (CoinGecko ~1-2 req/s).
THROTTLE_S = 1.2

CG = "https://api.coingecko.com/api/v3"

# RSS-ленты: (имя источника, url). Проверены вживую на keyless-доступ;
# те что блокируются (напр. CryptoSlate 403) честно уйдут в sources_failed.
RSS_FEEDS = [
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("BitcoinMagazine", "https://bitcoinmagazine.com/feed"),
    ("CryptoSlate", "https://cryptoslate.com/feed/"),
]

NEWS_PER_FEED = 6  # сколько свежих заголовков брать с каждой ленты
MOVERS_TOP_N = 8  # top gainers / losers
MARKETS_PER_PAGE = 250  # глубина выборки coins/markets для расчёта моверов
OKX_TOP_N = 10  # топ SWAP по обороту
GOOGLE_NEWS_TOP_N = 10  # keyless keyword news from Google News RSS
DEXSCREENER_TOP_N = 10  # top DEX pairs by 24h volume from checked queries
GOOGLE_NEWS_RSS_URL = (
    "https://news.google.com/rss/search"
    "?q=bitcoin%20OR%20crypto%20OR%20solana&hl=en-US&gl=US&ceid=US:en"
)
DEXSCREENER_QUERIES = ["SOL", "ETH", "BASE", "BTC"]


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(url, **kw):
    """GET с UA/таймаутом. Бросает на не-200 и сетевых ошибках (ловит вызывающий)."""
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# --------------------------------------------------------------------------- #
# CoinGecko: рынок (dominance / mcap / vol) + movers (trending / gainers / losers)
# --------------------------------------------------------------------------- #
def fetch_market(report):
    """CoinGecko /global -> срез структуры рынка."""
    try:
        d = _get(f"{CG}/global").json()["data"]
        time.sleep(THROTTLE_S)
        dom = d.get("market_cap_percentage", {})
        market = {
            "btc_dominance_pct": round(dom.get("btc", 0.0), 2),
            "eth_dominance_pct": round(dom.get("eth", 0.0), 2),
            "total_mcap_usd": d.get("total_market_cap", {}).get("usd"),
            "total_vol24h_usd": d.get("total_volume", {}).get("usd"),
            "mcap_change_24h_pct": round(
                d.get("market_cap_change_percentage_24h_usd", 0.0), 2
            ),
            "active_cryptocurrencies": d.get("active_cryptocurrencies"),
            "markets": d.get("markets"),
        }
        report["sources_ok"].append("coingecko_global")
        return market
    except Exception as e:
        report["sources_failed"].append(
            {"src": "coingecko_global", "reason": f"{type(e).__name__}: {e}"}
        )
        return {}


def fetch_trending(report):
    """CoinGecko /search/trending -> что ищут прямо сейчас."""
    try:
        d = _get(f"{CG}/search/trending").json()
        time.sleep(THROTTLE_S)
        out = []
        for c in d.get("coins", []):
            it = c.get("item", {})
            out.append(
                {
                    "symbol": (it.get("symbol") or "").upper(),
                    "name": it.get("name"),
                    "market_cap_rank": it.get("market_cap_rank"),
                    "price_btc": it.get("price_btc"),
                }
            )
        report["sources_ok"].append("coingecko_trending")
        return out
    except Exception as e:
        report["sources_failed"].append(
            {"src": "coingecko_trending", "reason": f"{type(e).__name__}: {e}"}
        )
        return []


def fetch_movers(report):
    """CoinGecko /coins/markets -> top по объёму + top gainers/losers 24h."""
    movers = {"gainers": [], "losers": [], "top_by_volume": []}
    try:
        rows = _get(
            f"{CG}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "volume_desc",
                "per_page": MARKETS_PER_PAGE,
                "page": 1,
                "price_change_percentage": "24h",
            },
        ).json()
        time.sleep(THROTTLE_S)

        def slim(r):
            return {
                "symbol": (r.get("symbol") or "").upper(),
                "name": r.get("name"),
                "price_usd": r.get("current_price"),
                "change_24h_pct": (
                    round(r["price_change_percentage_24h"], 2)
                    if r.get("price_change_percentage_24h") is not None
                    else None
                ),
                "vol24h_usd": r.get("total_volume"),
                "mcap_rank": r.get("market_cap_rank"),
            }

        movers["top_by_volume"] = [slim(r) for r in rows[:MOVERS_TOP_N]]

        with_chg = [r for r in rows if r.get("price_change_percentage_24h") is not None]
        by_chg = sorted(with_chg, key=lambda r: r["price_change_percentage_24h"])
        movers["losers"] = [slim(r) for r in by_chg[:MOVERS_TOP_N]]
        movers["gainers"] = [slim(r) for r in reversed(by_chg[-MOVERS_TOP_N:])]

        report["sources_ok"].append("coingecko_markets")
    except Exception as e:
        report["sources_failed"].append(
            {"src": "coingecko_markets", "reason": f"{type(e).__name__}: {e}"}
        )
    return movers


# --------------------------------------------------------------------------- #
# RSS новости
# --------------------------------------------------------------------------- #
def _parse_rss(content, src, limit):
    """Ручной парс RSS 2.0 через stdlib (без feedparser). Возвращает список item-ов."""
    root = ET.fromstring(content)
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")
    out = []
    for it in items[:limit]:
        title = it.findtext("title")
        link = it.findtext("link")
        pub = it.findtext("pubDate")
        if not title:
            continue
        out.append(
            {
                "src": src,
                "title": " ".join(title.split()).strip(),
                "time": (pub or "").strip() or None,
                "url": (link or "").strip() or None,
            }
        )
    return out


def fetch_news(report):
    """Собирает свежие заголовки со всех RSS-лент. Битые/блок — в sources_failed."""
    news = []
    for src, url in RSS_FEEDS:
        try:
            r = _get(url)
            items = _parse_rss(r.content, src, NEWS_PER_FEED)
            if not items:
                raise ValueError("no items parsed")
            news.extend(items)
            report["sources_ok"].append(f"rss_{src.lower()}")
        except Exception as e:
            report["sources_failed"].append(
                {"src": f"rss_{src.lower()}", "reason": f"{type(e).__name__}: {e}"}
            )
    return news


# --------------------------------------------------------------------------- #
# Google News RSS — keyword crypto news without API key
# --------------------------------------------------------------------------- #
def fetch_google_news(report):
    try:
        r = _get(GOOGLE_NEWS_RSS_URL)
        items = _parse_rss(r.content, "GoogleNews", GOOGLE_NEWS_TOP_N)
        if not items:
            raise ValueError("no items parsed")
        report["sources_ok"].append("google_news_rss")
        return items
    except Exception as e:
        report["sources_failed"].append(
            {"src": "google_news_rss", "reason": f"{type(e).__name__}: {e}"}
        )
        return []


# --------------------------------------------------------------------------- #
# DexScreener public API — DEX liquidity / pair context
# --------------------------------------------------------------------------- #
def _dex_pair_slim(row, observed_at):
    base = row.get("baseToken") or {}
    quote = row.get("quoteToken") or {}
    return {
        "observed_at_utc": observed_at,
        "chain": row.get("chainId"),
        "dex": row.get("dexId"),
        "pair": row.get("pairAddress"),
        "base_symbol": base.get("symbol"),
        "quote_symbol": quote.get("symbol"),
        "price_usd": row.get("priceUsd"),
        "liquidity_usd": (row.get("liquidity") or {}).get("usd"),
        "volume_24h_usd": (row.get("volume") or {}).get("h24"),
        "price_change_24h_pct": (row.get("priceChange") or {}).get("h24"),
        "pair_created_at_ms": row.get("pairCreatedAt"),
        "url": row.get("url"),
    }


def fetch_dexscreener(report):
    out = []
    seen = set()
    observed_at = _now_iso()
    try:
        for query in DEXSCREENER_QUERIES:
            d = _get(
                "https://api.dexscreener.com/latest/dex/search", params={"q": query}
            ).json()
            time.sleep(THROTTLE_S)
            for row in d.get("pairs") or []:
                key = (row.get("chainId"), row.get("pairAddress"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(_dex_pair_slim(row, observed_at))
        out.sort(key=lambda r: float(r.get("volume_24h_usd") or 0.0), reverse=True)
        report["sources_ok"].append("dexscreener_public")
        return out[:DEXSCREENER_TOP_N]
    except Exception as e:
        report["sources_failed"].append(
            {"src": "dexscreener_public", "reason": f"{type(e).__name__}: {e}"}
        )
        return []


# --------------------------------------------------------------------------- #
# Fear & Greed
# --------------------------------------------------------------------------- #
def fetch_fear_greed(report):
    try:
        d = _get("https://api.alternative.me/fng/", params={"limit": 1}).json()
        row = d["data"][0]
        fg = {
            "value": int(row["value"]),
            "classification": row.get("value_classification"),
            "ts_utc": dt.datetime.fromtimestamp(
                int(row["timestamp"]), dt.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        report["sources_ok"].append("fear_greed")
        return fg
    except Exception as e:
        report["sources_failed"].append(
            {"src": "fear_greed", "reason": f"{type(e).__name__}: {e}"}
        )
        return None


# --------------------------------------------------------------------------- #
# OKX public — структура нашего торгуемого рынка (топ SWAP по обороту)
# --------------------------------------------------------------------------- #
def fetch_okx(report):
    try:
        d = _get(
            "https://www.okx.com/api/v5/market/tickers", params={"instType": "SWAP"}
        ).json()
        if str(d.get("code")) != "0":
            raise ValueError(f"okx code={d.get('code')} msg={d.get('msg')}")
        rows = d.get("data", [])

        def turnover_usd(r):
            # USDT-SWAP: volCcy24h = объём в БАЗОВОЙ монете (контрактах), не в USDT.
            # USD-оборот = volCcy24h * last. (проверено по ctVal/ctValCcy инструментов)
            try:
                return float(r.get("volCcy24h") or 0.0) * float(r.get("last") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def chg_pct(r):
            try:
                last = float(r["last"])
                sod = float(r["sodUtc0"])
                return round((last / sod - 1.0) * 100.0, 2) if sod else None
            except (TypeError, ValueError, KeyError, ZeroDivisionError):
                return None

        rows.sort(key=turnover_usd, reverse=True)
        top = [
            {
                "instId": r.get("instId"),
                "last": (float(r["last"]) if r.get("last") else None),
                "turnover24h_usd": round(turnover_usd(r), 2),
                "change_24h_pct": chg_pct(r),
            }
            for r in rows[:OKX_TOP_N]
        ]

        report["sources_ok"].append("okx_public")
        return {"instType": "SWAP", "n_instruments": len(rows), "top_by_turnover": top}
    except Exception as e:
        report["sources_failed"].append(
            {"src": "okx_public", "reason": f"{type(e).__name__}: {e}"}
        )
        return {}


# --------------------------------------------------------------------------- #
# Сборка бандла
# --------------------------------------------------------------------------- #
def build_bundle():
    report = {"sources_ok": [], "sources_failed": []}

    market = fetch_market(report)
    trending = fetch_trending(report)
    movers = fetch_movers(report)
    movers["trending"] = trending
    news = fetch_news(report)
    google_news = fetch_google_news(report)
    dexscreener = fetch_dexscreener(report)
    fear_greed = fetch_fear_greed(report)
    okx = fetch_okx(report)

    notes = [
        "Keyless public sources only. No API keys / no auth used.",
        "Deterministic data-puller; LLM summary is a separate operator step.",
        "RSS pubDate strings are source-native (not normalized to UTC).",
        f"Failed/blocked sources are listed honestly ({len(report['sources_failed'])} this run).",
    ]

    bundle = {
        "ts_utc": _now_iso(),
        "sources_ok": report["sources_ok"],
        "sources_failed": report["sources_failed"],
        "market": market,
        "movers": movers,
        "news": news,
        "google_news": google_news,
        "dexscreener": dexscreener,
        "fear_greed": fear_greed,
        "okx": okx,
        "notes": notes,
    }
    return bundle


def _fmt_usd(v):
    if v is None:
        return "n/a"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:.2f}{unit}"
    return f"${v:.2f}"


def render_md(b):
    L = []
    L.append("# Crypto Infofield Bundle (SCOUT #1)")
    L.append("")
    L.append(f"- Generated (UTC): {b['ts_utc']}")
    L.append(
        f"- Sources OK ({len(b['sources_ok'])}): {', '.join(b['sources_ok']) or '—'}"
    )
    if b["sources_failed"]:
        fails = ", ".join(
            f"{f['src']} ({f['reason'].split(':')[0]})" for f in b["sources_failed"]
        )
        L.append(f"- Sources FAILED ({len(b['sources_failed'])}): {fails}")
    else:
        L.append("- Sources FAILED (0): —")
    L.append("")

    m = b.get("market") or {}
    if m:
        L.append("## Market structure (CoinGecko /global)")
        L.append(
            f"- BTC dominance: {m.get('btc_dominance_pct')}%  |  ETH dominance: {m.get('eth_dominance_pct')}%"
        )
        L.append(
            f"- Total mcap: {_fmt_usd(m.get('total_mcap_usd'))}  (24h change: {m.get('mcap_change_24h_pct')}%)"
        )
        L.append(f"- Total 24h volume: {_fmt_usd(m.get('total_vol24h_usd'))}")
        L.append(
            f"- Active cryptos: {m.get('active_cryptocurrencies')}  |  markets: {m.get('markets')}"
        )
        L.append("")

    fg = b.get("fear_greed")
    if fg:
        L.append("## Fear & Greed")
        L.append(
            f"- {fg.get('value')} / 100 — {fg.get('classification')}  (as of {fg.get('ts_utc')})"
        )
        L.append("")

    mv = b.get("movers") or {}

    def _tbl(rows):
        for r in rows:
            L.append(
                f"  - {r.get('symbol'):<8} {str(r.get('change_24h_pct')):>8}%  "
                f"price={r.get('price_usd')}  vol24h={_fmt_usd(r.get('vol24h_usd'))}"
            )

    if mv.get("gainers"):
        L.append("## Top gainers 24h (CoinGecko, vol-ranked universe)")
        _tbl(mv["gainers"])
        L.append("")
    if mv.get("losers"):
        L.append("## Top losers 24h")
        _tbl(mv["losers"])
        L.append("")
    if mv.get("top_by_volume"):
        L.append("## Top by 24h volume")
        _tbl(mv["top_by_volume"])
        L.append("")
    if mv.get("trending"):
        L.append("## Trending searches (CoinGecko)")
        for t in mv["trending"]:
            L.append(
                f"  - {t.get('symbol'):<8} {t.get('name')}  (rank {t.get('market_cap_rank')})"
            )
        L.append("")

    okx = b.get("okx") or {}
    if okx.get("top_by_turnover"):
        L.append(
            f"## OKX SWAP top by 24h turnover ({okx.get('n_instruments')} instruments)"
        )
        for r in okx["top_by_turnover"]:
            L.append(
                f"  - {r.get('instId'):<18} {str(r.get('change_24h_pct')):>8}%  "
                f"last={r.get('last')}  turnover={_fmt_usd(r.get('turnover24h_usd'))}"
            )
        L.append("")

    if b.get("news"):
        L.append(f"## Latest headlines ({len(b['news'])})")
        for n in b["news"]:
            L.append(f"  - [{n['src']}] {n['title']}")
            L.append(f"      {n.get('time')}  {n.get('url')}")
        L.append("")

    if b.get("google_news"):
        L.append(f"## Google News crypto search ({len(b['google_news'])})")
        for n in b["google_news"]:
            L.append(f"  - {n['title']}")
            L.append(f"      {n.get('time')}  {n.get('url')}")
        L.append("")

    if b.get("dexscreener"):
        L.append(f"## DexScreener top pairs by 24h volume ({len(b['dexscreener'])})")
        for r in b["dexscreener"]:
            L.append(
                f"  - {r.get('chain')}/{r.get('dex')} {r.get('base_symbol')}-{r.get('quote_symbol')}  "
                f"liq={_fmt_usd(r.get('liquidity_usd'))}  vol24h={_fmt_usd(r.get('volume_24h_usd'))}  "
                f"chg24h={r.get('price_change_24h_pct')}"
            )
        L.append("")

    if b.get("notes"):
        L.append("## Notes")
        for n in b["notes"]:
            L.append(f"- {n}")
        L.append("")

    return "\n".join(L)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    bundle = build_bundle()

    with open(BUNDLE_JSON, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
    with open(BUNDLE_MD, "w", encoding="utf-8") as f:
        f.write(render_md(bundle))

    # --- краткое summary в stdout ---
    m = bundle.get("market") or {}
    mv = bundle.get("movers") or {}
    fg = bundle.get("fear_greed")
    print("=== SCOUT #1 digest collected ===")
    print(f"ts_utc:        {bundle['ts_utc']}")
    print(
        f"sources OK:    {len(bundle['sources_ok'])} -> {', '.join(bundle['sources_ok'])}"
    )
    if bundle["sources_failed"]:
        print(
            f"sources FAIL:  {len(bundle['sources_failed'])} -> "
            + "; ".join(f"{f['src']}: {f['reason']}" for f in bundle["sources_failed"])
        )
    else:
        print("sources FAIL:  0")
    if m:
        print(
            f"market:        BTC.dom={m.get('btc_dominance_pct')}% ETH.dom={m.get('eth_dominance_pct')}% "
            f"mcap={_fmt_usd(m.get('total_mcap_usd'))} (24h {m.get('mcap_change_24h_pct')}%) "
            f"vol={_fmt_usd(m.get('total_vol24h_usd'))}"
        )
    if fg:
        print(f"fear&greed:    {fg.get('value')}/100 {fg.get('classification')}")
    if mv.get("gainers"):
        g = mv["gainers"][0]
        loser = mv["losers"][0] if mv.get("losers") else {}
        print(
            f"top gainer:    {g.get('symbol')} {g.get('change_24h_pct')}%   "
            f"top loser: {loser.get('symbol')} {loser.get('change_24h_pct')}%"
        )
    if mv.get("trending"):
        print(
            f"trending:      {', '.join(t.get('symbol') for t in mv['trending'][:7])}"
        )
    print(f"news:          {len(bundle.get('news', []))} headlines")
    print(f"google news:   {len(bundle.get('google_news', []))} headlines")
    print(f"dexscreener:   {len(bundle.get('dexscreener', []))} pairs")
    print(f"bundle JSON:   {BUNDLE_JSON}")
    print(f"bundle MD:     {BUNDLE_MD}")


if __name__ == "__main__":
    main()
