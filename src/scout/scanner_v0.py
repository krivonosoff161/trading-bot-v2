# -*- coding: utf-8 -*-
"""
scanner_v0.py — первая живая петля инфо-эдж сканера (end-to-end карточка).

Поток (SCANNER_SPEC.md, лок V0):
  Cointelegraph RSS → fire-on-new дедуп (scanner_seen.json) → page_extract
  → scout_analyst.generate_scout_card (GO/NO-GO) → scanner_journal.jsonl
  → (опц.) Telegram в ЛИЧНЫЙ чат.

ГРАНИЦА (держать): paper-only. НЕ импортирует okx_client.place_market_order /
auto_execute — путь до денег физически отсутствует. .env только читаем (ключи),
не пишем. Telegram-доставка ВКЛючается только если задан SCANNER_CHAT_ID
(иначе dry-доставка: печать в консоль) — чтобы НЕ слать сырые карточки клиентам
продукта-аналитика (broadcast не используется).

Запуск:
  python src/scout/scanner_v0.py --dry-run         # плумбинг без LLM/Telegram
  python src/scout/scanner_v0.py --limit 1         # 1 живая карточка
  python src/scout/scanner_v0.py                    # до 3 новых карточек
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

import os  # noqa: E402

from src.scout.page_extract import extract                       # noqa: E402
from src.scout.scout_analyst import generate_scout_card          # noqa: E402
from src.scout import scanner_journal as J                       # noqa: E402
from src.utils.telegram import send_message_to, send_photo_to    # noqa: E402
from src.strategy.chart_renderer import render_chart             # noqa: E402  (чистый matplotlib, без ордер-движка)

# ── Конфиг V0 ────────────────────────────────────────────────────────────────
RSS_URL = "https://cointelegraph.com/rss"          # лок V0: прямой publisher-link
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-v0; keyless)"}
TIMEOUT = 20
DEFAULT_LIMIT = 3
LAYER = 1                                           # V0 = крипта-история (хардкод)
TRIGGER = "rss_headline"

SEEN_PATH = _ROOT / "logs" / "scout" / "scanner_seen.json"
BUNDLE_PATH = _ROOT / "logs" / "scout" / "bundle_latest.json"
SCANNER_CHAT_ID = os.getenv("SCANNER_CHAT_ID", "").strip("'\"")

# актив → OKX instId (word-boundary матч в заголовке/тексте). V0 = мажоры.
TRACKED = [
    ("BTC", "BTC-USDT-SWAP", r"\b(bitcoin|btc)\b"),
    ("ETH", "ETH-USDT-SWAP", r"\b(ethereum|ether|eth)\b"),
    ("SOL", "SOL-USDT-SWAP", r"\b(solana|sol)\b"),
    ("XRP", "XRP-USDT-SWAP", r"\b(xrp|ripple)\b"),
    ("BNB", "BNB-USDT-SWAP", r"\b(bnb)\b"),
]


# ── seen-store (fire-on-new) ─────────────────────────────────────────────────
def load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")


# ── источники / контекст ─────────────────────────────────────────────────────
def fetch_rss(limit: int = 30) -> list[dict]:
    r = requests.get(RSS_URL, headers=UA, timeout=TIMEOUT)
    # детект блокировки: HTML вместо XML (gap аудита — чтоб отказ был ВИДЕН)
    head = r.content[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise RuntimeError(f"feed returned HTML, not RSS ({RSS_URL}) — источник заблокирован")
    root = ET.fromstring(r.content)
    ch = root.find("channel")
    items = (ch.findall("item") if ch is not None else root.findall(".//item"))[:limit]
    out = []
    for it in items:
        title = " ".join((it.findtext("title") or "").split()).strip()
        url = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip() or None
        if title and url:
            out.append({"title": title, "url": url, "time": pub})
    return out


def match_asset(text: str) -> tuple[str | None, str | None]:
    low = text.lower()
    for sym, inst, pat in TRACKED:
        if re.search(pat, low):
            return sym, inst
    return None, None


def okx_last(inst_id: str | None) -> float | None:
    if not inst_id:
        return None
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker",
                         params={"instId": inst_id}, headers=UA, timeout=TIMEOUT)
        d = r.json()
        if str(d.get("code")) == "0" and d.get("data"):
            return float(d["data"][0]["last"])
    except Exception:
        return None
    return None


CHARTS_DIR = _ROOT / "logs" / "scout" / "charts"


def fetch_candles(inst_id: str, bar: str = "15m", limit: int = 120) -> list | None:
    try:
        r = requests.get("https://www.okx.com/api/v5/market/candles",
                         params={"instId": inst_id, "bar": bar, "limit": str(limit)},
                         headers=UA, timeout=TIMEOUT)
        d = r.json()
        if str(d.get("code")) == "0" and d.get("data"):
            return d["data"]            # newest-first OKX rows [ts,o,h,l,c,vol,...]
    except Exception:
        return None
    return None


def _bar_for_horizon(h) -> str:
    """Таймфрейм графика под горизонт анализа (инфо-эдж = дни, НЕ 15m-скальп)."""
    try:
        h = float(h)
    except (TypeError, ValueError):
        return "4H"
    if h <= 24:
        return "1H"
    if h <= 96:
        return "4H"
    return "1D"


def make_chart(inst_id: str, captured_at: str, row: dict) -> str | None:
    """График актива: NO_GO/WATCH = чистый, GO = с прорисовкой уровней. Путь или None.
    Таймфрейм — под горизонт анализа (1H/4H/1D), не 15m."""
    bar = _bar_for_horizon(row.get("horizon_hours"))
    candles = fetch_candles(inst_id, bar=bar)
    if not candles or len(candles) < 30:
        return None
    try:
        last_close = float(candles[0][4])
    except Exception:
        return None
    indicators = {"15m": {"close": last_close, "swing_highs": [], "swing_lows": []}}
    lv = row.get("levels") or {}
    if row.get("verdict") == "GO" and lv.get("entry"):
        levels = {"entry_price": lv.get("entry"), "sl": lv.get("invalidation"), "tp1": lv.get("target")}
        direction = "buy" if row.get("side") == "long" else "sell"
        entry_signal = "ENTRY"
    else:
        levels, direction, entry_signal = None, None, "NO_TRADE"
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    out = CHARTS_DIR / f"{row['card_id']}.png"
    try:
        render_chart(symbol=inst_id, raw_15m=candles, indicators=indicators,
                     captured_at=captured_at, output_path=str(out),
                     levels=levels, entry_signal=entry_signal, direction=direction,
                     tf_label=bar)
    except Exception as e:
        print(f"  chart: {e}")
        return None
    return str(out) if out.exists() else None


def market_ctx_line() -> str | None:
    if not BUNDLE_PATH.exists():
        return None
    try:
        b = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    fg = b.get("fear_greed") or {}
    mk = b.get("market") or {}
    bits = []
    if fg.get("value") is not None:
        bits.append(f"настроение {fg.get('classification')} ({fg['value']}/100)")
    if mk.get("mcap_change_24h_pct") is not None:
        bits.append(f"капа рынка за сутки {mk['mcap_change_24h_pct']:+.1f}%")
    if mk.get("btc_dominance_pct"):
        bits.append(f"BTC.dom {mk['btc_dominance_pct']:.0f}%")
    return ", ".join(bits) or None


# ── карточка для Telegram ────────────────────────────────────────────────────
def _meaningful(v) -> bool:
    return bool(v) and str(v).strip().lower() not in ("none", "no", "unknown", "n/a", "-", "нет")


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _cap(v, n: int) -> str:
    s = str(v or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def format_card(row: dict) -> str:
    """HTML-подпись под график: кликабельный источник, флаги значимые, GO — с уровнями.
    Поля подрезаны, чтобы уложиться в лимит подписи Telegram (1024)."""
    emoji = {"GO": "🟢", "NO_GO": "🔴", "WATCH": "🟡"}.get(row["verdict"], "⚪")
    side = {"long": "LONG", "short": "SHORT", "none": ""}.get(row.get("side", "none"), "")
    clean_url = (row.get("source_url") or "").split("?")[0]
    lv = row.get("levels") or {}
    is_go = row.get("verdict") == "GO"

    L = [
        f"🛰️ <b>СКАНЕР · {_esc(row.get('asset') or '—')}</b>",
        "",
        f"📰 <b>{_esc(_cap(row.get('headline'), 150))}</b>",
        "",
        _esc(_cap(row.get("summary"), 280)),
        "",
        f"{emoji} <b>{_esc((row['verdict'] + ' ' + side).strip())}</b>",
    ]
    if is_go and lv.get("entry"):
        L.append(f"📍 вход ~{_esc(lv.get('entry'))} · стоп {_esc(lv.get('invalidation'))} · цель {_esc(lv.get('target'))}")
    if not is_go and _meaningful(row.get("in_price")):
        L.append(f"в цене?: {_esc(_cap(row['in_price'], 110))}")
    if _meaningful(row.get("red_flag")):
        L.append(f"⛔ red-flag: {_esc(_cap(row['red_flag'], 130))}")
    if _meaningful(row.get("invalidation")):
        L.append(f"инвалидация: {_esc(_cap(row['invalidation'], 150))}")
    L.append(f"прогноз ({_esc(row['horizon_hours'])}ч): {_esc(_cap(row.get('forecast'), 170))}")
    if row.get("low_confidence"):
        L.append("⚠️ тело не извлечено — низкая уверенность")
    L += ["", "⚠️ <i>paper · фильтр, не рекомендация</i>",
          f'<a href="{_esc(clean_url)}">📎 источник</a>']
    return "\n".join(x for x in L if x is not None)


# ── одна карточка (async — LLM + Telegram) ───────────────────────────────────
async def process_item(item: dict, mline: str | None, dry: bool,
                       btc_ref: float | None = None) -> dict | None:
    headline = item["title"]
    url = item["url"]

    # 1) тело страницы + ветка деградации
    ext = extract(url) if not dry else {"text": "(dry-run)", "date": item.get("time")}
    low_conf = False
    if not ext or ext.get("error") or len(ext.get("text") or "") < 200:
        low_conf = True
        body_text = ""
        source_ts = (ext or {}).get("date") or item.get("time") or J.now_iso()[:10]
    else:
        body_text = ext["text"]
        source_ts = ext.get("date") or item.get("time") or J.now_iso()[:10]

    # 2) актив + цена (матч ТОЛЬКО по заголовку — новость про актив называет его в title;
    #    матч по телу ловил мусор, напр. SOL в статье про Zcash)
    asset, inst = match_asset(headline)
    if asset is None:
        return {"skipped": "no_tracked_asset", "headline": headline}
    price = okx_last(inst) if not dry else None

    news = {"headline": headline, "text": body_text, "date": source_ts, "url": url}

    # 3) мозг (GO/NO-GO) — в dry-run заглушка, без токенов
    if dry:
        fields = {
            "asset": asset, "catalyst": "(dry)", "in_price": "(dry)", "red_flag": "none",
            "mechanics": "none", "surprise": "unknown", "verdict": "NO_GO", "side": "none",
            "asymmetry": "нет", "invalidation": "(dry)", "forecast": "(dry)",
            "horizon_hours": 48, "summary": "DRY-RUN: плумбинг без LLM", "low_confidence": low_conf,
        }
    else:
        fields = await generate_scout_card(
            news, layer=LAYER, trigger=TRIGGER,
            asset_hint=asset, market_ctx_line=mline, low_confidence=low_conf,
            price=price,
        )
        if not fields:
            return {"skipped": "llm_failed", "headline": headline}

    # 4) строка журнала
    row = J.build_row(
        source_url=url, source_ts=source_ts, layer=LAYER,
        asset=fields.get("asset") or asset, trigger_type=TRIGGER, headline=headline,
        verdict=fields["verdict"], horizon_hours=fields["horizon_hours"],
        price_at_decision=price, okx_inst=inst, btc_at_decision=btc_ref,
        levels=fields.get("levels"),
        catalyst=fields.get("catalyst", ""), in_price=fields.get("in_price", ""),
        red_flag=fields.get("red_flag", ""), mechanics=fields.get("mechanics", ""),
        surprise=fields.get("surprise", ""), side=fields.get("side", "none"),
        asymmetry=fields.get("asymmetry", ""), invalidation=fields.get("invalidation", ""),
        forecast=fields.get("forecast", ""), summary=fields.get("summary", ""),
        low_confidence=fields.get("low_confidence", low_conf),
        outcome_source=("okx" if price is not None else "manual"),
    )
    cid = ("DRY-" + J.card_id_for(url)) if dry else J.write_row(row)
    card = format_card(row)

    # 5) график: NO_GO/WATCH = чистый, GO = с прорисовкой уровней
    chart_path = make_chart(inst, J.now_iso(), row) if (cid and not dry and inst) else None

    # 6) доставка (фото с подписью; текст-фоллбек если графика нет)
    sent = None
    if cid and SCANNER_CHAT_ID and not dry:
        try:
            if chart_path:
                await send_photo_to(SCANNER_CHAT_ID, chart_path, caption=card, parse_mode="HTML")
                sent = "photo"
            else:
                sent = await send_message_to(SCANNER_CHAT_ID, card)  # card уже HTML
        except Exception as e:
            print(f"  telegram: {e}")

    return {"card_id": cid, "row": row, "card": card, "sent": sent,
            "asset": asset, "price": price}


# ── оркестрация (одиночный проход) ───────────────────────────────────────────
async def run(limit: int, dry: bool) -> None:
    J.ensure_pending_store()
    seen = load_seen()
    mline = market_ctx_line()
    btc_ref = okx_last("BTC-USDT-SWAP") if not dry else None   # якорь baseline на проход
    print(f"=== scanner_v0 {'(DRY-RUN)' if dry else ''} | layer={LAYER} | "
          f"telegram={'ON '+SCANNER_CHAT_ID if (SCANNER_CHAT_ID and not dry) else 'OFF (dry-доставка)'} ===")
    print(f"рыночный фон: {mline or '—'}")

    try:
        items = fetch_rss()
    except Exception as e:
        print(f"RSS ОШИБКА: {e}")
        return

    fresh = [it for it in items if it["url"] not in seen]
    print(f"RSS: {len(items)} заголовков, {len(fresh)} новых (не в seen)\n")

    made = 0
    for it in fresh:
        if made >= limit:
            break
        res = await process_item(it, mline, dry, btc_ref=btc_ref)
        seen.add(it["url"])          # помечаем виденным независимо от исхода
        if not res:
            continue
        if res.get("skipped"):
            print(f"  · скип [{res['skipped']}]: {res['headline'][:65]}")
            continue
        made += 1
        r = res["row"]
        sent = f"sent msg_id={res['sent']}" if res.get("sent") else ("dry-доставка" if not dry else "не отправлено")
        print(f"\n[{made}] {r['verdict']} {r['side']} · {r['asset']} @ {res.get('price')} · "
              f"card_id={res['card_id'] or 'DUP'} · {sent}")
        print("    " + "\n    ".join(res["card"].splitlines()))

    if not dry:                       # dry ничего не персистит (не съедает seen-слоты)
        save_seen(seen)
    print(f"\n=== готово: {made} карточек · seen={len(seen)}"
          f"{' (dry — не сохранён)' if dry else ''} · журнал={J.JOURNAL} ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="плумбинг без LLM/Telegram")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="макс карточек за проход")
    args = ap.parse_args()
    asyncio.run(run(limit=args.limit, dry=args.dry_run))


if __name__ == "__main__":
    main()
