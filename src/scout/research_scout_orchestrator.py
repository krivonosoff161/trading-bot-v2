# -*- coding: utf-8 -*-
"""
research_scout_orchestrator.py — собирает research-скаутов в один прогон и
АППЕНДИТ одну дневную строку в растущий forward-series датасет под будущий OOS.

Что делает (всё KEYLESS, read-only к рынку, пишет ТОЛЬКО в digest_data/):
  1. Запускает SCOUT #1 (daily_digest_collector.build_bundle) -> свежий крипто-бандл
     (CoinGecko global/movers/trending, RSS, Fear&Greed, OKX public).
  2. Тянет KEYLESS поток-контекст SCOUT #2 (DeFiLlama):
       - stablecoin total mcap + 24h net-mint (capital в/из крипты),
       - DeFi TVL,
       - DEX 24h volume.
  3. (опц, keyless) CoinGecko community sentiment по топ-монетам
     (sentiment_votes_up_percentage), если эндпоинт отвечает.
  4. АППЕНДИТ ОДНУ дневную строку в digest_data/forward_series.csv.
     Idempotent по дате (date_utc): одна строка на день; перезапуск ОБНОВЛЯЕТ
     сегодняшнюю строку, не плодит дубли.
  5. Пишет объединённый bundle digest_data/orchestrator_bundle_latest.json для
     LLM-сводки.
  6. Печатает краткий статус (сколько строк в forward_series + сегодняшние цифры).

ПРИНЦИПЫ: нет .env / ключей / auth / прода / денег. Graceful skip упавших
источников — оркестратор не падает, недостающие поля = пустые в CSV.

ЭТО ИНФРАСТРУКТУРА НАКОПЛЕНИЯ. Она НЕ обещает эдж. Она копит честный датасет
signal(сегодня) -> forward-price(через недели) для последующей OOS-проверки.

Запуск:
    python scripts/analysis/research/research_scout_orchestrator.py
    python scripts/analysis/research/research_scout_orchestrator.py --no-sentiment
"""

import sys
import io
import os
import csv
import json
import time
import datetime as dt

import requests

# SCOUT #1 переиспользуем напрямую (та же папка).
# ВАЖНО: scout #1 на импорте САМ оборачивает sys.stdout в UTF-8 TextIOWrapper.
# Поэтому импортируем его ПЕРВЫМ и НЕ оборачиваем stdout повторно — двойная
# обёртка над одним buffer рвёт поток при teardown ("I/O operation on closed
# file"). После этого импорта sys.stdout уже UTF-8 — нам делать ничего не надо.
import daily_digest_collector as scout1  # noqa: E402

# Доп.страховка на случай если поведение scout#1 изменится (idempotent).
if "TextIOWrapper" not in type(sys.stdout).__name__ and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Хранилище данных новой системы: logs/scout/ (src/scout/ -> ../.. = корень проекта).
_SCOUT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT_DIR = os.path.join(_SCOUT_ROOT, "logs", "scout")
FORWARD_CSV = os.path.join(OUT_DIR, "forward_series.csv")
ORCH_BUNDLE_JSON = os.path.join(OUT_DIR, "orchestrator_bundle_latest.json")

UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 research-scout-orchestrator; keyless)"}
TIMEOUT = 25
THROTTLE_S = 1.2

CG = "https://api.coingecko.com/api/v3"

# Колонки forward_series.csv (порядок фиксирован — это растущий датасет).
CSV_FIELDS = [
    "date_utc",
    "btc_dom",
    "eth_dom",
    "total_mcap_usd",
    "total_vol24h_usd",
    "fear_greed",
    "stablecoin_mcap_usd",
    "stablecoin_net_mint_24h_usd",
    "defi_tvl_usd",
    "dex_vol24h_usd",
    "btc_price",
    "eth_price",
    "n_gainers_gt10pct",
    "n_losers_gt10pct",
    "top_gainer_sym",
    "top_gainer_pct",
]

# Монеты для опционального community-sentiment зонда (keyless CoinGecko /coins/{id}).
SENTIMENT_COINS = ["bitcoin", "ethereum", "solana"]


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _get_json(url, report, label, **kw):
    """Keyless GET -> json. Падение источника -> graceful skip (None + лог в report)."""
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
        r.raise_for_status()
        report["sources_ok"].append(label)
        return r.json()
    except Exception as e:
        report["sources_failed"].append({"src": label, "reason": f"{type(e).__name__}: {e}"})
        return None


# --------------------------------------------------------------------------- #
# SCOUT #2 — KEYLESS flow context (DeFiLlama)
# --------------------------------------------------------------------------- #
def fetch_flow_context(report):
    """
    Stablecoin total mcap + 24h net-mint, DeFi TVL, DEX 24h vol — всё keyless.
    Каждый источник скипается независимо (graceful).
    """
    flow = {
        "stablecoin_mcap_usd": None,
        "stablecoin_net_mint_24h_usd": None,
        "defi_tvl_usd": None,
        "dex_vol24h_usd": None,
    }

    # --- Stablecoins: total circulating mcap + 24h net-mint (fresh capital) ---
    d = _get_json(
        "https://stablecoins.llama.fi/stablecoins?includePrices=true",
        report, "defillama_stablecoins",
    )
    if isinstance(d, dict):
        pegged = d.get("peggedAssets", []) or []
        total = 0.0
        net_mint = 0.0
        for a in pegged:
            circ = (a.get("circulating", {}) or {}).get("peggedUSD", 0) or 0
            prev = (a.get("circulatingPrevDay", {}) or {}).get("peggedUSD", 0) or 0
            total += circ
            net_mint += (circ - prev)  # >0 = капитал заходит в крипту (mint)
        flow["stablecoin_mcap_usd"] = round(total, 2)
        flow["stablecoin_net_mint_24h_usd"] = round(net_mint, 2)
    time.sleep(THROTTLE_S)

    # --- DeFi TVL (capital parked on-chain) ---
    d = _get_json(
        "https://api.llama.fi/v2/historicalChainTvl",
        report, "defillama_tvl",
    )
    if isinstance(d, list) and d:
        last = d[-1]
        tvl = last.get("tvl")
        if tvl is not None:
            flow["defi_tvl_usd"] = round(float(tvl), 2)
    time.sleep(THROTTLE_S)

    # --- DEX 24h volume (on-chain trading activity) ---
    d = _get_json(
        "https://api.llama.fi/overview/dexs"
        "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true",
        report, "defillama_dex",
    )
    if isinstance(d, dict):
        v = d.get("total24h")
        if v is not None:
            flow["dex_vol24h_usd"] = round(float(v), 2)

    return flow


# --------------------------------------------------------------------------- #
# Optional KEYLESS community sentiment (CoinGecko /coins/{id})
# --------------------------------------------------------------------------- #
def fetch_sentiment(report):
    """
    sentiment_votes_up_percentage по топ-монетам, если эндпоинт отвечает.
    Чисто опционально — не входит в CSV (CoinGecko режет это поле без ключа на
    части окружений), копится в bundle для контекста. Graceful skip.
    """
    out = {}
    for cid in SENTIMENT_COINS:
        d = _get_json(
            f"{CG}/coins/{cid}",
            report, f"coingecko_sentiment_{cid}",
            params={
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
        )
        if isinstance(d, dict):
            up = d.get("sentiment_votes_up_percentage")
            down = d.get("sentiment_votes_down_percentage")
            if up is not None or down is not None:
                out[cid] = {"votes_up_pct": up, "votes_down_pct": down}
        time.sleep(THROTTLE_S)
    return out


# --------------------------------------------------------------------------- #
# Derive the single daily forward-series row from the merged bundle
# --------------------------------------------------------------------------- #
def _coin_price_from_movers(movers, want_sym):
    """Достаём цену монеты (BTC/ETH) из movers.top_by_volume (CoinGecko)."""
    for r in (movers.get("top_by_volume") or []):
        if (r.get("symbol") or "").upper() == want_sym:
            return r.get("price_usd")
    return None


def build_row(s1_bundle, flow):
    """Сводит дневную строку CSV из бандла scout #1 + flow-контекста scout #2."""
    m = s1_bundle.get("market") or {}
    mv = s1_bundle.get("movers") or {}
    fg = s1_bundle.get("fear_greed") or {}

    gainers = mv.get("gainers") or []
    losers = mv.get("losers") or []

    n_gainers = sum(1 for r in gainers
                    if r.get("change_24h_pct") is not None and r["change_24h_pct"] > 10)
    n_losers = sum(1 for r in losers
                   if r.get("change_24h_pct") is not None and r["change_24h_pct"] < -10)

    top_gainer = gainers[0] if gainers else {}

    row = {
        "date_utc": _today_utc(),
        "btc_dom": m.get("btc_dominance_pct"),
        "eth_dom": m.get("eth_dominance_pct"),
        "total_mcap_usd": m.get("total_mcap_usd"),
        "total_vol24h_usd": m.get("total_vol24h_usd"),
        "fear_greed": fg.get("value"),
        "stablecoin_mcap_usd": flow.get("stablecoin_mcap_usd"),
        "stablecoin_net_mint_24h_usd": flow.get("stablecoin_net_mint_24h_usd"),
        "defi_tvl_usd": flow.get("defi_tvl_usd"),
        "dex_vol24h_usd": flow.get("dex_vol24h_usd"),
        "btc_price": _coin_price_from_movers(mv, "BTC"),
        "eth_price": _coin_price_from_movers(mv, "ETH"),
        "n_gainers_gt10pct": n_gainers,
        "n_losers_gt10pct": n_losers,
        "top_gainer_sym": top_gainer.get("symbol"),
        "top_gainer_pct": top_gainer.get("change_24h_pct"),
    }
    return row


# --------------------------------------------------------------------------- #
# Idempotent CSV append (one row per date_utc; rerun updates today's row)
# --------------------------------------------------------------------------- #
def upsert_row(row):
    """
    Аппендит/обновляет строку по date_utc. Возвращает (action, total_rows):
      action in {"appended", "updated"}.
    """
    rows = []
    if os.path.exists(FORWARD_CSV):
        with open(FORWARD_CSV, "r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                rows.append(r)

    # Нормализуем все значения к str для CSV (None -> "").
    def _norm(v):
        return "" if v is None else str(v)

    new_row = {k: _norm(row.get(k)) for k in CSV_FIELDS}

    action = "appended"
    replaced = False
    for i, r in enumerate(rows):
        if r.get("date_utc") == new_row["date_utc"]:
            rows[i] = new_row
            replaced = True
            action = "updated"
            break
    if not replaced:
        rows.append(new_row)

    # Сортируем по дате (стабильный растущий датасет).
    rows.sort(key=lambda r: r.get("date_utc", ""))

    # Атомарная запись (через .tmp -> replace), чтобы не побить файл при сбое.
    tmp = FORWARD_CSV + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
    os.replace(tmp, FORWARD_CSV)

    return action, len(rows)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    do_sentiment = "--no-sentiment" not in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)

    report = {"sources_ok": [], "sources_failed": []}

    print("=" * 70)
    print("RESEARCH SCOUT ORCHESTRATOR")
    print("=" * 70)

    # --- SCOUT #1: full crypto infofield bundle (reuse) ---
    print("\n[1/4] SCOUT #1 — crypto infofield bundle ...")
    s1_bundle = scout1.build_bundle()
    report["sources_ok"].extend(s1_bundle.get("sources_ok", []))
    report["sources_failed"].extend(s1_bundle.get("sources_failed", []))
    print(f"      scout#1 sources OK: {len(s1_bundle.get('sources_ok', []))}, "
          f"failed: {len(s1_bundle.get('sources_failed', []))}")

    # --- SCOUT #2: keyless flow context ---
    print("\n[2/4] SCOUT #2 — keyless flow context (DeFiLlama) ...")
    flow = fetch_flow_context(report)
    print(f"      stablecoin_mcap={flow['stablecoin_mcap_usd']} "
          f"net_mint_24h={flow['stablecoin_net_mint_24h_usd']} "
          f"tvl={flow['defi_tvl_usd']} dex_vol={flow['dex_vol24h_usd']}")

    # --- Optional sentiment ---
    sentiment = {}
    if do_sentiment:
        print("\n[3/4] (opt) CoinGecko community sentiment ...")
        sentiment = fetch_sentiment(report)
        print(f"      sentiment coins resolved: {list(sentiment.keys()) or '— (skipped/blocked)'}")
    else:
        print("\n[3/4] sentiment skipped (--no-sentiment)")

    # --- Build + upsert the daily forward-series row ---
    print("\n[4/4] forward_series.csv upsert ...")
    row = build_row(s1_bundle, flow)
    action, total_rows = upsert_row(row)

    # --- Merged bundle for LLM summary ---
    merged = {
        "ts_utc": _now_iso(),
        "date_utc": row["date_utc"],
        "sources_ok": report["sources_ok"],
        "sources_failed": report["sources_failed"],
        "forward_row": row,
        "scout1": s1_bundle,
        "scout2_flow": flow,
        "sentiment": sentiment,
        "notes": [
            "Keyless public sources only. No API keys / auth / .env / prod / money.",
            "forward_series.csv is a GROWING signal->forward-price dataset for FUTURE OOS.",
            "This is accumulation infrastructure; it does NOT imply an edge today.",
            "Idempotent per date_utc: one row/day; rerun updates today's row.",
            f"Failed/blocked sources listed honestly ({len(report['sources_failed'])} this run).",
        ],
    }
    with open(ORCH_BUNDLE_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    # --- Status ---
    print("\n" + "=" * 70)
    print("STATUS")
    print("=" * 70)
    print(f"forward_series.csv : {FORWARD_CSV}")
    print(f"  row action       : {action}  (total rows now: {total_rows})")
    print(f"orchestrator bundle: {ORCH_BUNDLE_JSON}")
    ok = sorted(set(report["sources_ok"]))
    print(f"sources OK ({len(ok)}) : {', '.join(ok)}")
    if report["sources_failed"]:
        print(f"sources FAILED ({len(report['sources_failed'])}):")
        for fobj in report["sources_failed"]:
            print(f"   - {fobj['src']}: {fobj['reason']}")
    else:
        print("sources FAILED (0)")

    print("\nToday's key figures:")
    print(f"  date_utc            : {row['date_utc']}")
    print(f"  BTC.dom / ETH.dom   : {row['btc_dom']}% / {row['eth_dom']}%")
    print(f"  total mcap / vol24h : {scout1._fmt_usd(row['total_mcap_usd'])} / "
          f"{scout1._fmt_usd(row['total_vol24h_usd'])}")
    print(f"  fear & greed        : {row['fear_greed']}")
    print(f"  stablecoin mcap     : {scout1._fmt_usd(row['stablecoin_mcap_usd'])}")
    print(f"  stablecoin net-mint : {scout1._fmt_usd(row['stablecoin_net_mint_24h_usd'])} (24h)")
    print(f"  DeFi TVL / DEX vol  : {scout1._fmt_usd(row['defi_tvl_usd'])} / "
          f"{scout1._fmt_usd(row['dex_vol24h_usd'])}")
    print(f"  BTC / ETH price     : {row['btc_price']} / {row['eth_price']}")
    print(f"  gainers>10% / losers<-10% : {row['n_gainers_gt10pct']} / {row['n_losers_gt10pct']}")
    print(f"  top gainer          : {row['top_gainer_sym']} {row['top_gainer_pct']}%")


if __name__ == "__main__":
    main()
