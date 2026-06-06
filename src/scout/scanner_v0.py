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
from urllib.parse import urlsplit, urlunsplit

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
# (chief = src/scout/agents/chief.py через orchestrator; старый generate_scout_card больше не зовём)
from src.scout.router import (route_asset, route_temporal, score_materiality,   # noqa: E402
                              dedup_config, limits_config)
from src.scout.dedup import is_duplicate, event_key as make_event_key          # noqa: E402
from src.scout.sources.okx_listings import fetch_new_listings                   # noqa: E402
from src.scout.agents import orchestrator                                       # noqa: E402
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

# Активы/слои/материальность/источники — в config/*.yaml (читает router.py), не хардкод.
ROUTER_VERSION = "v1"          # config-driven router (entities.yaml)
RATE_RUB_PER_1K = 0.5          # анкер стоимости Яндекса (приблизит., для llm_budget)


def canonical_url(url: str) -> str:
    """Нормализовать URL (drop query/utm/fragment, lowercase host, strip slash) — дедуп utm-вариантов."""
    try:
        p = urlsplit((url or "").strip())
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", "")) or url
    except Exception:
        return url


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
                       btc_ref: float | None = None,
                       recent: list | None = None, dedup_min: int = 88,
                       carded: dict | None = None, max_per_asset: int = 1) -> dict | None:
    headline = item["title"]
    url = item.get("url")
    pre_routed = bool(item.get("asset"))      # листинг = актив/слой известны из инструмента
    lead_class = item.get("lead_class", "LAGGING")
    source_class = item.get("source_class", "rss")
    source = item.get("source", "cointelegraph")

    # 1) РОУТЕР актив/слой (+ материальность для RSS; листинг pre-routed, материален by design)
    if pre_routed:
        asset, inst = item["asset"], item.get("okx_inst")
        layer, conf = int(item.get("layer", 2)), 1.0
        baseline_sym = item.get("baseline")
        mat = {"family": item.get("event_type", "unclassified"), "score": 0.6}
        phase = item.get("phase", "REALIZED")
    else:
        routed = route_asset(headline)
        if not routed:
            return {"skipped": "no_tracked_asset", "headline": headline}
        asset, inst, layer = routed["asset"], routed["okx_inst"], routed["layer"]
        conf, baseline_sym = routed["confidence"], routed.get("baseline")
        # V0: режем ТОЛЬКО заведомый шум (noise_genre); no_material_term пропускаем в LLM
        # (рой: не резать сюрприз вслепую пока журнал мал; V1 ужесточит).
        mat = score_materiality(headline, layer)
        if mat.get("drop_reason") == "noise_genre":
            return {"skipped": "noise_genre", "headline": headline, "asset": asset}
        phase = route_temporal(headline)["phase"]

    # CONTEXT (ценовой обзор/мнение/прогноз) — не событие → не будим LLM (экономия токенов)
    if phase == "CONTEXT":
        return {"skipped": "context_commentary", "headline": headline, "asset": asset}
    # КАП — не флудить одним активом за проход (был кейс 4 BTC NO_GO подряд)
    if carded is not None and carded.get(asset, 0) >= max_per_asset:
        return {"skipped": "asset_capped", "headline": headline, "asset": asset}

    # EVENT-ДЕДУП — то же событие из N лент/проходов → 1 карточка
    if recent is not None and is_duplicate(headline, asset, recent, dedup_min):
        return {"skipped": "dup_event", "headline": headline, "asset": asset}
    canon = canonical_url(url or f"https://www.okx.com/trade-swap/{(inst or asset or '').lower()}")
    ekey = make_event_key(asset, headline)

    # 3) тело — только для новостей со статьёй (у листинга статьи нет → анализ по заголовку)
    if url and not pre_routed:
        ext = extract(url) if not dry else {"text": "(dry-run)", "date": item.get("time")}
        if not ext or ext.get("error") or len(ext.get("text") or "") < 200:
            low_conf, body_text = True, ""
            source_ts = (ext or {}).get("date") or item.get("time") or J.now_iso()[:10]
        else:
            low_conf, body_text = False, ext["text"]
            source_ts = ext.get("date") or item.get("time") or J.now_iso()[:10]
    else:
        low_conf, body_text = True, ""
        source_ts = item.get("time") or J.now_iso()[:10]

    # baseline-цена ПО СЛОЮ (excess vs index): BTC из btc_ref, иначе снять (None = manual, off-OKX)
    if baseline_sym == "BTC-USDT-SWAP":
        baseline_price = btc_ref
    elif baseline_sym and not dry:
        baseline_price = okx_last(baseline_sym)
    else:
        baseline_price = None

    price = okx_last(inst) if (not dry and inst) else None
    news = {"headline": headline, "text": body_text, "date": source_ts, "url": url or canon}

    # 3) ОРКЕСТРАТОР: дешёвый слой-агент → правила → chief (только кандидаты). dry = заглушка без LLM.
    if dry:
        orch = {"decision": "journal", "verdict": "NO_GO", "side": "none", "chief_called": False,
                "agent": {"direction": "none", "confidence": None, "phase": phase, "asset": asset,
                          "event_type": mat.get("family") or "unknown", "materiality": mat.get("score"),
                          "red_flags": [], "mechanics": [], "key_facts": []},
                "chief": None, "usage": [], "send_channel": True}
    else:
        orch = await orchestrator.process(news, asset, layer, lead_class, price, mline)

    if orch.get("decision") == "trash":
        return {"skipped": "trash_lowmat", "headline": headline, "asset": asset}

    agent = orch.get("agent") or {}
    ch = orch.get("chief") or {}
    verdict, side = orch.get("verdict", "NO_GO"), orch.get("side", "none")

    # ПРЕДОХРАНИТЕЛЬ (в КОДЕ): GO+сторона только если LEADING (анти-мираж запаздывающего эджа)
    if verdict == "GO" and side != "none" and lead_class != "LEADING":
        verdict = "WATCH"

    tokens = sum(int(u.get("total_tokens") or 0) for u in (orch.get("usage") or []))
    last_usage = (ch.get("_usage") or (orch.get("usage") or [{}])[-1] or {})
    fields = {
        "horizon_hours": ch.get("horizon_hours", 48), "levels": ch.get("levels"),
        "catalyst": "; ".join(map(str, agent.get("key_facts") or []))[:200],
        "in_price": ch.get("in_price", ""),
        "red_flag": ", ".join(map(str, agent.get("red_flags") or [])) or "none",
        "mechanics": ", ".join(map(str, agent.get("mechanics") or [])) or "none",
        "surprise": ch.get("surprise") or agent.get("phase", ""),
        "asymmetry": ch.get("asymmetry", ""), "invalidation": ch.get("invalidation", ""),
        "forecast": ch.get("forecast", ""),
        "summary": ch.get("summary") or "низкая материальность — в журнал (датасет), chief не звали",
    }

    # 4) строка журнала
    row = J.build_row(
        source_url=canon, source_ts=source_ts, layer=layer,
        asset=agent.get("asset") or asset,
        trigger_type=("okx_listing" if pre_routed else TRIGGER), headline=headline,
        verdict=verdict, horizon_hours=fields["horizon_hours"],
        price_at_decision=price, okx_inst=inst, btc_at_decision=baseline_price,
        baseline_symbol=baseline_sym,
        lead_class=lead_class, source=source, source_class=source_class, router_version=ROUTER_VERSION,
        asset_confidence=conf,
        event_type=agent.get("event_type") or mat.get("family") or "unclassified",
        event_phase=agent.get("phase") or phase,
        materiality_score=(agent.get("materiality") if agent.get("materiality") is not None else mat.get("score")),
        event_key=ekey,
        chief_called=orch.get("chief_called", False),
        agent_direction=agent.get("direction", "none"), agent_confidence=agent.get("confidence"),
        llm_provider=last_usage.get("provider"), llm_model=last_usage.get("model"),
        levels=fields["levels"],
        catalyst=fields["catalyst"], in_price=fields["in_price"],
        red_flag=fields["red_flag"], mechanics=fields["mechanics"],
        surprise=fields["surprise"], side=side,
        asymmetry=fields["asymmetry"], invalidation=fields["invalidation"],
        forecast=fields["forecast"], summary=fields["summary"],
        low_confidence=low_conf,
        outcome_source=("okx" if price is not None else "manual"),
    )
    cid = ("DRY-" + J.card_id_for(canon)) if dry else J.write_row(row)
    card = format_card(row)

    # 5+6) график + доставка — ТОЛЬКО chief-карточки (send_channel); дешёвый NO_GO = только журнал/датасет
    sent = None
    if cid and orch.get("send_channel") and SCANNER_CHAT_ID and not dry:
        chart_path = make_chart(inst, J.now_iso(), row) if inst else None
        try:
            if chart_path:
                await send_photo_to(SCANNER_CHAT_ID, chart_path, caption=card, parse_mode="HTML")
                sent = "photo"
            else:
                sent = await send_message_to(SCANNER_CHAT_ID, card)
        except Exception as e:
            print(f"  telegram: {e}")

    return {"card_id": cid, "row": row, "card": card, "sent": sent,
            "asset": asset, "price": price, "canon": canon, "tokens": tokens,
            "chief_called": orch.get("chief_called"), "channel": bool(orch.get("send_channel"))}


# ── оркестрация (одиночный проход) ───────────────────────────────────────────
async def run(limit: int, dry: bool) -> None:
    J.ensure_pending_store()
    seen = load_seen()
    mline = market_ctx_line()
    btc_ref = okx_last("BTC-USDT-SWAP") if not dry else None   # якорь baseline на проход
    dcfg = dedup_config()
    dedup_min = int(dcfg.get("fuzzy_min", 88))
    recent = J.recent_events(int(dcfg.get("window_hours", 48)))   # окно event-дедупа
    max_per_asset = int(limits_config().get("max_cards_per_asset_per_run", 1))
    carded: dict = {}                                            # кап карточек на актив за проход
    print(f"=== scanner_v0 {'(DRY-RUN)' if dry else ''} | RSS+листинги | "
          f"telegram={'ON '+SCANNER_CHAT_ID if (SCANNER_CHAT_ID and not dry) else 'OFF (dry-доставка)'} ===")
    print(f"рыночный фон: {mline or '—'}")

    try:
        rss_items = fetch_rss()
        for it in rss_items:                      # тег источника (RSS = запаздывающий)
            it.update({"source": "cointelegraph", "lead_class": "LAGGING", "source_class": "rss"})
    except Exception as e:
        print(f"RSS ОШИБКА: {e}")
        rss_items = []
    listings = [] if dry else fetch_new_listings(within_hours=24, limit=5)
    items = listings + rss_items                  # опережающие листинги первыми
    fresh = [it for it in items if canonical_url(it.get("url") or "") not in seen]
    if not dry and fresh:                         # полный аудит: лог КАЖДОГО входящего до фильтров
        J.write_ingest([{"canon": canonical_url(it.get("url") or ""), "source": it.get("source", "?"),
                         "headline": it.get("title"), "url": it.get("url")} for it in fresh])
    print(f"источники: листинги(LEADING)={len(listings)} + RSS={len(rss_items)} | новых={len(fresh)}\n")

    made = n_dropped = n_llm_fail = total_tokens = 0
    for it in fresh:
        if made >= limit:
            break
        canon = canonical_url(it.get("url") or "")
        res = await process_item(it, mline, dry, btc_ref=btc_ref,
                                 recent=recent, dedup_min=dedup_min,
                                 carded=carded, max_per_asset=max_per_asset)
        skipped = res.get("skipped") if res else None
        # retry-minimal: НЕ помечаем seen при временном сбое LLM (повторим следующий проход)
        if skipped != "llm_failed":
            seen.add(canon)
        if not res:
            continue
        total_tokens += res.get("tokens", 0)
        if skipped:
            if skipped == "llm_failed":
                n_llm_fail += 1
            else:                            # фильтр-дроп (роутер/материальность) → анти-survivorship
                n_dropped += 1
                if not dry:
                    stage = {"no_tracked_asset": "router", "dup_event": "dedup",
                         "context_commentary": "gate", "asset_capped": "cap"}.get(skipped, "materiality")
                    J.write_drop(it["url"], it["title"], skipped, asset=res.get("asset"), drop_stage=stage)
            print(f"  · скип [{skipped}]: {res['headline'][:65]}")
            continue
        made += 1
        r = res["row"]
        recent.append((res.get("asset"), r["headline"]))   # within-run дубли тоже ловим
        carded[res["asset"]] = carded.get(res["asset"], 0) + 1   # кап на актив
        sent = f"sent msg_id={res['sent']}" if res.get("sent") else ("dry-доставка" if not dry else "не отправлено")
        print(f"\n[{made}] {r['verdict']} {r['side']} · {r['asset']} L{r['layer']} {r['lead_class']}"
              f" · {r['source']} · @ {res.get('price')} · {r['event_phase']} · card_id={res['card_id'] or 'DUP'} · {sent}")
        print("    " + "\n    ".join(res["card"].splitlines()))

    cost = round(total_tokens / 1000 * RATE_RUB_PER_1K, 2)
    if not dry:                       # dry ничего не персистит (не съедает seen-слоты)
        save_seen(seen)
        J.write_budget({"n_ingested": len(items), "n_fresh": len(fresh), "n_cards": made,
                        "n_dropped": n_dropped, "n_llm_fail": n_llm_fail,
                        "total_tokens": total_tokens, "cost_rub": cost})
    print(f"\n=== готово: {made} карточек · seen={len(seen)}"
          f"{' (dry — не сохранён)' if dry else ''} · токенов={total_tokens} (~{cost}₽) · журнал={J.JOURNAL} ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="плумбинг без LLM/Telegram")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="макс карточек за проход")
    args = ap.parse_args()
    asyncio.run(run(limit=args.limit, dry=args.dry_run))


if __name__ == "__main__":
    main()
