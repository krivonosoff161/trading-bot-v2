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
import datetime as dt
import email.utils
import html
import json
import math
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from src.utils.runtime_root import load_runtime_dotenv

    load_runtime_dotenv(_ROOT)
except Exception:
    pass

from src.scout.page_extract import extract                       # noqa: E402
from src.scout import google_news_url as GN                      # noqa: E402
# (chief = src/scout/agents/chief.py через orchestrator; старый generate_scout_card больше не зовём)
from src.scout.router import (route_asset, route_temporal, score_materiality,   # noqa: E402
                              dedup_config, limits_config, enabled_sources, source_meta)
from src.scout.dedup import is_duplicate, event_key as make_event_key          # noqa: E402
from src.scout import news_buffer as NB                                        # noqa: E402
from src.scout.sources.okx_listings import fetch_new_listings                   # noqa: E402
from src.scout.sources.okx_announcements import fetch_okx_announcements         # noqa: E402
from src.scout.sources.okx_market_tape import fetch_okx_market_tape             # noqa: E402
from src.scout.sources.sec_edgar import fetch_recent_filings                    # noqa: E402
from src.scout.sources.dexscreener import fetch_alt_flow_signals                # noqa: E402
from src.scout.sources.goplus_rugcheck import fetch_token_risk_signals          # noqa: E402
from src.scout.sources.token_unlocks import fetch_upcoming_unlocks              # noqa: E402
from src.scout.sources.btc_eth_tactical import fetch_btc_eth_tactical           # noqa: E402
from src.scout.sources.fred_calendar import fetch_fred_calendar                 # noqa: E402
from src.scout.sources.eia import fetch_eia_schedule                           # noqa: E402
from src.scout.sources.opec import fetch_opec_schedule                         # noqa: E402
from src.scout.sources.earnings_calendar import fetch_earnings_calendar        # noqa: E402
from src.scout.sources.etf_flow import l1_context_line                          # noqa: E402
from src.scout.sources.telegram_web import fetch_telegram_web_sources           # noqa: E402
from src.scout.delivery_policy import scanner_telegram_enabled                 # noqa: E402
from src.scout.agents import orchestrator                                       # noqa: E402
from src.scout import trigger_context as TC                                     # noqa: E402
from src.scout.temporal import classify_temporal                                # noqa: E402
from src.scout import scanner_journal as J                       # noqa: E402
from src.scout import scanner_records as R                       # noqa: E402
from src.scout import pending_store as PS                        # noqa: E402
from src.scout import watch_queue as WQ                          # noqa: E402
from src.scout.price_provider import get_price as _get_price     # noqa: E402
from src.utils import llm_budget_guard as LBG                    # noqa: E402
from src.research_lab.product_progress import publish_checkpoint, scanner_metrics  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.rcc_startup_evidence import (  # noqa: E402
    rcc_run_identity_from_environment,
)
from src.utils.telegram import chat_ids, send_message_to, send_photo_to    # noqa: E402
from src.strategy.chart_renderer import render_chart             # noqa: E402  (чистый matplotlib, без ордер-движка)

# ── Конфиг ───────────────────────────────────────────────────────────────────
RSS_FEEDS = [   # FALLBACK (имя, url) если реестр пуст; основной список — source_registry.yaml
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
    ("googlenews", "https://news.google.com/rss/search?q=crypto+OR+bitcoin+OR+altcoin+OR+ethereum&hl=en-US&gl=US&ceid=US:en"),
]
UA = {"User-Agent": "Mozilla/5.0 (trading-bot-v2 scanner-v0; keyless)"}
TIMEOUT = 20
DEFAULT_LIMIT = 3
MAX_CANONICAL_PASS_SECONDS = 540.0
MAX_RESOLVE_SECONDS = 240.0
LAYER = 1                                           # V0 = крипта-история (хардкод)
TRIGGER = "rss_headline"

def _scanner_runtime_paths(root: Path) -> dict[str, Path]:
    """Return the scanner's mutable paths below one validated private root."""
    state_root = resolve_private_root(root) / "logs" / "scout"
    return {
        "seen": state_root / "scanner_seen.json",
        "bundle": state_root / "bundle_latest.json",
        "delivery": state_root / "telegram_delivery.jsonl",
        "chief_retry": state_root / "chief_retry_state.json",
        "charts": state_root / "charts",
    }


def _configure_runtime_paths(root: Path) -> None:
    """Bind mutable scanner paths only after the private root is validated."""
    global SEEN_PATH, BUNDLE_PATH, TELEGRAM_DELIVERY_LOG, CHIEF_RETRY_PATH, CHARTS_DIR
    private_root = resolve_private_root(root)
    paths = _scanner_runtime_paths(private_root)
    SEEN_PATH = paths["seen"]
    BUNDLE_PATH = paths["bundle"]
    TELEGRAM_DELIVERY_LOG = paths["delivery"]
    CHIEF_RETRY_PATH = paths["chief_retry"]
    CHARTS_DIR = paths["charts"]
    J.configure_private_root(private_root)
    R.configure_private_root(private_root)
    PS.configure_private_root(private_root)
    WQ.configure_private_root(private_root)
    NB.configure_private_root(private_root)


# Direct helper use remains private by default. Canonical `run` and `main`
# rebind these paths to the validated configured root before any side effect.
_DEFAULT_SCANNER_PATHS = _scanner_runtime_paths(DEFAULT_PRIVATE_ROOT)
SEEN_PATH = _DEFAULT_SCANNER_PATHS["seen"]
BUNDLE_PATH = _DEFAULT_SCANNER_PATHS["bundle"]
TELEGRAM_DELIVERY_LOG = _DEFAULT_SCANNER_PATHS["delivery"]
SCANNER_CHAT_ID = os.getenv("SCANNER_CHAT_ID", "").strip("'\"")


def _scanner_chat_ids() -> list[str]:
    """Notification channel targets: canonical env first, legacy scanner env as fallback."""
    ids = chat_ids("TELEGRAM_NOTIFICATION_CHAT_ID") or chat_ids("SCANNER_CHAT_ID")
    if ids:
        return ids
    return [SCANNER_CHAT_ID] if SCANNER_CHAT_ID else []

# Активы/слои/материальность/источники — в config/*.yaml (читает router.py), не хардкод.
ROUTER_VERSION = "v1"          # config-driven router (entities.yaml)
RATE_RUB_PER_1K = 0.5          # fallback, если usage не вернул cost_rub


def env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_seconds(name: str, default: float, ceiling: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        value = default
    if not math.isfinite(value):
        value = default
    return max(1.0, min(value, ceiling))


def _product_root() -> Path:
    configured = os.environ.get("TRADING_BOT_RESEARCH_ROOT", "").strip()
    return resolve_private_root(Path(configured) if configured else DEFAULT_PRIVATE_ROOT)


def _scanner_stop_requested() -> bool:
    return (_product_root() / "state" / "STOP_NEWS_SCANNER.txt").exists()


def _publish_scanner_progress(
    *, stage: str, completed_chunks: int, **metrics: int | float | bool
) -> None:
    completed_at = dt.datetime.now(tz=dt.timezone.utc).timestamp()
    publish_checkpoint(
        _product_root(),
        component="scanner_progress",
        sequence=max(1, int(completed_at * 1_000_000)),
        status="progress",
        metrics={
            "stage": stage,
            "completed_chunks": int(completed_chunks),
            **metrics,
        },
        completed_at=completed_at,
        rcc_run=rcc_run_identity_from_environment(),
    )


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


def write_telegram_delivery(event: dict) -> None:
    """Append a delivery audit row without exposing token/chat secrets."""
    payload = {
        "ts": J.now_iso(),
        "card_id": event.get("card_id"),
        "asset": event.get("asset"),
        "verdict": event.get("verdict"),
        "source": event.get("source"),
        "send_channel": bool(event.get("send_channel")),
        "has_chat_id": bool(_scanner_chat_ids()),
        "target_count": len(_scanner_chat_ids()),
        "dry": bool(event.get("dry")),
        "status": event.get("status"),
        "reason": event.get("reason"),
        "message_id": event.get("message_id"),
        "error": event.get("error"),
    }
    TELEGRAM_DELIVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with TELEGRAM_DELIVERY_LOG.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


# ── chief-error retry (P0 аудита 11.06: кандидат не умирает тихим NO_GO) ────
CHIEF_RETRY_PATH = _DEFAULT_SCANNER_PATHS["chief_retry"]
CHIEF_RETRY_MAX = 2     # ретраев ПОСЛЕ первой попытки (итого до 3 заходов к chief)


def _load_retry_state() -> dict:
    if not CHIEF_RETRY_PATH.exists():
        return {}
    try:
        return dict(json.loads(CHIEF_RETRY_PATH.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _save_retry_state(state: dict) -> None:
    CHIEF_RETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHIEF_RETRY_PATH.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True),
                                encoding="utf-8")


def chief_retry_decision(key: str, retry_max: int = CHIEF_RETRY_MAX) -> str:
    """chief упал на кандидате → 'retry' (вернуть в очередь) или 'finalize' (кап исчерпан).

    Счётчик персистентный (chief_retry_state.json): процесс сканера перезапускается
    каждый проход. На finalize ключ чистится."""
    state = _load_retry_state()
    n = int(state.get(key) or 0) + 1
    if n <= retry_max:
        state[key] = n
        _save_retry_state(state)
        return "retry"
    state.pop(key, None)
    _save_retry_state(state)
    return "finalize"


def parse_source_ts(raw: str | None) -> dt.datetime | None:
    """Best-effort parser for source timestamps from RSS/pubDate/extractors."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z") and "T" in s:
            return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        if len(s) == 10 and s.count("-") == 2:
            return dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        parsed = email.utils.parsedate_to_datetime(s)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def source_age_limit_hours(source: str, lead_class: str, source_class: str) -> int | None:
    """Drop obviously stale lagging RSS before it burns LLM tokens.

    Registry may set max_age_hours explicitly. Otherwise:
    - LEADING / non-RSS: no age gate
    - aggregator RSS: 72h
    - other lagging RSS: 168h
    """
    meta = source_meta(source)
    if meta.get("max_age_hours") is not None:
        try:
            return int(meta["max_age_hours"])
        except (TypeError, ValueError):
            return None
    if str(lead_class).upper() == "LEADING" or source_class != "rss":
        return None
    if str(meta.get("trust") or "").lower() == "aggregator":
        return 72
    return 168


def is_stale_story(source_ts: str | None, source: str, lead_class: str, source_class: str,
                   now_utc: dt.datetime | None = None) -> bool:
    limit_h = source_age_limit_hours(source, lead_class, source_class)
    if not limit_h:
        return False
    ts = parse_source_ts(source_ts)
    if ts is None:
        return False
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    age_h = (now_utc - ts.astimezone(dt.timezone.utc)).total_seconds() / 3600
    return age_h > limit_h


# ── источники / контекст ─────────────────────────────────────────────────────
def rss_sources() -> list[tuple[str, str, str]]:
    """Активные RSS-источники (name, url, lead_class) из реестра; fallback — встроенные."""
    out = [(name, meta["url"], meta.get("lead_class", "LAGGING"))
           for name, meta in enabled_sources().items()
           if meta.get("source_class") == "rss" and meta.get("url")]
    return out or [(n, u, "LAGGING") for n, u in RSS_FEEDS]


def fetch_rss(limit: int = 25) -> list[dict]:
    """Тянет активные RSS-ленты ИЗ РЕЕСТРА (широкий охват). Каждый item тегается source/lead_class."""
    out = []
    for name, url, lead in rss_sources():
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            head = r.content[:200].lstrip().lower()
            if head.startswith(b"<!doctype") or head.startswith(b"<html"):
                print(f"  RSS {name}: HTML вместо XML — пропуск (заблокирован)")
                continue
            root = ET.fromstring(r.content)
            ch = root.find("channel")
            items = (ch.findall("item") if ch is not None else root.findall(".//item"))[:limit]
            for it in items:
                title = " ".join((it.findtext("title") or "").split()).strip()
                u = (it.findtext("link") or "").strip()
                pub = (it.findtext("pubDate") or "").strip() or None
                if title and u:
                    out.append({"title": title, "url": u, "time": pub, "source": name,
                                "lead_class": lead, "source_class": "rss"})
        except Exception as e:
            print(f"  RSS {name}: {e}")
    return out


def okx_last(inst_id: str | None) -> float | None:
    """Backward-compatible price fetch (returns price or None)."""
    price, _reason = _get_price(inst_id)
    return price


def okx_last_with_reason(inst_id: str | None) -> tuple[float | None, str]:
    """Price fetch with explicit reason for unavailability."""
    return _get_price(inst_id)


def farm_resolve_enabled() -> bool:
    """OKX instrument resolution + data-readiness for WATCH/GO rows (off by default).

    Enabled by the scanner loop launchers (SCANNER_FARM_RESOLVE=true); kept off in
    unit tests and the default no-buffer path so they never hit the public network.
    """
    return env_enabled("SCANNER_FARM_RESOLVE")


def enrich_trigger(item: dict, asset: str | None, inst: str | None,
                   body_text: str | None, use_buffer: bool) -> dict:
    """Verify the OKX instrument + assess candle readiness → build_row farm kwargs.

    Public/paper-only and lazily imported so the scanner import tree stays minimal
    (the no-order-path isolation guard never loads the research-lab chain). Any
    failure degrades to {} so a card never depends on the resolver being up.
    """
    from src.scout import trigger_enrichment as TE
    from src.research_lab.market_data_provider import get_provider
    try:
        provider = get_provider("okx-public")
    except Exception:
        provider = None
    mentions_fn = (lambda a: NB.recent_mentions(a, limit=6)) if use_buffer else None
    pkg = TE.build_trigger_package(
        asset=asset, okx_inst=inst, asset_class=item.get("asset_class"),
        headline=item.get("title"), body_text=body_text, source=item.get("source"),
        extraction_status=item.get("extraction_status"),
        list_time_ms=item.get("list_time_ms"),
        provider=provider, mentions_fn=mentions_fn,
    )
    return TE.package_to_journal_fields(pkg)


CHARTS_DIR = _DEFAULT_SCANNER_PATHS["charts"]


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
def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().strip("'\"").lower() in ("1", "true", "yes", "on")


def should_send_to_channel(verdict: str, send_channel: bool) -> bool:
    """Гейт доставки: в канал идут только GO/WATCH chief-карточки.

    NO_GO остаётся в журнале/датасете (канал не флудим); вернуть NO_GO в канал
    можно ЯВНО через env SCANNER_SEND_NO_GO=true (дефолт — тихо)."""
    if not send_channel:
        return False
    v = str(verdict or "").upper()
    if v in ("GO", "WATCH"):
        return True
    return v == "NO_GO" and _env_flag("SCANNER_SEND_NO_GO")


def _meaningful(v) -> bool:
    return bool(v) and str(v).strip().lower() not in ("none", "no", "unknown", "n/a", "-", "нет")


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _cap(v, n: int) -> str:
    s = str(v or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _money(v) -> str:
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return "—"


def _side_ru(v: str | None) -> str:
    return {"long": "лонг", "short": "шорт", "none": "нет сделки"}.get(str(v or "none").lower(), "нет сделки")


def _in_price_ru(v: str | None) -> str:
    return {"yes": "да", "partial": "частично", "no": "нет", "unknown": "неясно"}.get(
        str(v or "unknown").lower(), str(v or "неясно")
    )


LAYER_LABELS = {1: "крипта/мажоры", 2: "альты/мемы", 3: "металлы", 4: "энергия/нефть", 5: "акции/pre-IPO"}


def layer_label(layer) -> str:
    """Человеческая метка слоя для карточки (вместо системного L1..L5)."""
    try:
        return LAYER_LABELS.get(int(layer), f"L{layer}")
    except (TypeError, ValueError):
        return "—"


_FARM_PENDING_RU = {
    "fresh_listing_pending": "свежий листинг — ждём свечей",
    "too_short": "мало свечей — отложено до накопления",
    "missing": "нет свечей на OKX — отложено",
    "provider_error": "данные OKX временно недоступны",
    "no_okx_instrument": "нет торгуемого инструмента на OKX",
    "readiness_not_assessed": "готовность данных не проверена",
}


def farm_status_ru(row: dict) -> str | None:
    """Human exchange/data status for a card (None when resolution was not run)."""
    if row.get("okx_resolved") is None:
        return None
    if not row.get("okx_resolved"):
        return _FARM_PENDING_RU["no_okx_instrument"]
    if row.get("farm_eligible"):
        tf = row.get("selected_timeframe")
        return f"данные готовы · расчёт ТФ {tf}" if tf else "данные готовы · расчёт"
    reason = str(row.get("farm_pending_reason") or row.get("data_readiness_status") or "")
    return _FARM_PENDING_RU.get(reason, "данные не готовы — отложено")


def format_caption(row: dict) -> str:
    """Минимальная подпись к графику; смысловой разбор идет отдельным сообщением."""
    tf = _bar_for_horizon(row.get("horizon_hours"))
    return f"📈 <b>График · {_esc(row.get('asset') or '—')} · {tf}</b>"


def format_card(row: dict) -> str:
    """Telegram-карточка трейдеру: слой-метка + вердикт-специфичная структура.

    GO — уровни/сценарий/отмена; WATCH — почему наблюдаем/что подтвердит/что отменит;
    NO_GO — компактная диагностика (в канал по дефолту не идёт — логи/override).
    Пустые/none-поля не печатаем."""
    verdict = str(row.get("verdict") or "").upper()
    emoji = {"GO": "🟢", "NO_GO": "🔴", "WATCH": "🟡"}.get(verdict, "⚪")
    clean_url = (row.get("source_url") or "").split("?")[0]
    lv = row.get("levels") or {}
    side = str(row.get("side") or "none").lower()

    head = f"🛰️ <b>{_esc(row.get('asset') or '—')} · {_esc(layer_label(row.get('layer')))}</b>"
    verdict_line = f"{emoji} <b>{_esc(verdict)}</b>"
    if side in ("long", "short"):
        verdict_line += f" · {_esc(_side_ru(side))}"

    L = [head, verdict_line, "", f"{_esc(_cap(row.get('summary'), 760))}"]

    if verdict == "WATCH":
        if _meaningful(row.get("asymmetry")):
            L.append(f"<b>Почему наблюдаем:</b> {_esc(_cap(row.get('asymmetry'), 520))}")
        if _meaningful(row.get("forecast")):
            L.append(f"<b>Что подтвердит:</b> {_esc(_cap(row.get('forecast'), 520))}")
        if _meaningful(row.get("invalidation")):
            L.append(f"<b>Что отменит:</b> {_esc(_cap(row.get('invalidation'), 520))}")
    elif verdict == "GO":
        if _meaningful(row.get("asymmetry")):
            L.append(f"<b>Асимметрия:</b> {_esc(_cap(row.get('asymmetry'), 520))}")
        if _meaningful(row.get("forecast")):
            L.append(f"<b>Сценарий:</b> {_esc(_cap(row.get('forecast'), 520))}")
        if _meaningful(row.get("invalidation")):
            L.append(f"<b>Отмена:</b> {_esc(_cap(row.get('invalidation'), 520))}")
        if lv.get("entry"):
            L.append(f"<b>Уровни:</b> вход ~{_esc(lv.get('entry'))} · стоп {_esc(lv.get('invalidation'))} · цель {_esc(lv.get('target'))}")
    else:  # NO_GO/прочее — компактная диагностика без сценарных полей
        if _meaningful(row.get("asymmetry")):
            L.append(f"<b>Почему мимо:</b> {_esc(_cap(row.get('asymmetry'), 320))}")

    meta_bits = []
    if verdict in ("GO", "WATCH"):
        if row.get("price_at_decision") is not None:
            meta_bits.append(f"цена {_esc(_money(row.get('price_at_decision')))}")
        if row.get("horizon_hours"):
            meta_bits.append(f"горизонт ~{_esc(row.get('horizon_hours'))}ч")
        ip = str(row.get("in_price") or "").lower()
        if ip in ("yes", "partial"):
            meta_bits.append(f"в цене: {_in_price_ru(ip)}")
    if meta_bits:
        L.append("<i>" + " · ".join(meta_bits) + "</i>")

    # Биржа/данные: торгуемый инструмент + готовность свечей + статус фермы (GO/WATCH).
    if verdict in ("GO", "WATCH"):
        fs = farm_status_ru(row)
        if fs:
            inst_bit = ""
            if row.get("okx_resolved") and row.get("okx_inst"):
                cls = row.get("okx_asset_class")
                inst_bit = f" · {row.get('okx_inst')}" + (f" ({cls})" if cls else "")
            L.append(f"<b>Биржа/данные:</b> {_esc(fs)}{_esc(inst_bit)}")

    if row.get("low_confidence"):
        L.append("⚠️ оценка по заголовку (полный текст источника недоступен)")
    if _meaningful(row.get("headline")):
        L.append(f"<b>Оригинал:</b> {_esc(_cap(row.get('headline'), 220))}")

    L += ["", "⚠️ <i>paper · фильтр, не рекомендация</i>",
          f'<a href="{_esc(clean_url)}">📎 источник</a>']
    return _cap("\n".join(x for x in L if x is not None), 3900)


def write_routing_snapshot(
    *,
    item: dict,
    source: str,
    source_cfg: dict,
    allowed_layers: list[int],
    asset: str | None,
    layer: int | None,
    asset_confidence: float | None,
    source_phase_prior: str,
    headline_phase: str,
    final_phase: str | None,
    materiality_score: float | None,
    materiality_family: str | None,
    verdict: str | None = None,
    chief_called: bool | None = None,
    escalation_gate: str | None = None,
    skipped: str | None = None,
    note: str | None = None,
    veto_flags: list | None = None,
    no_edge_flags: list | None = None,
    context_found: bool | None = None,
    context_missing: list | None = None,
) -> None:
    J.write_routing_audit(
        {
            "source": source,
            "source_class": item.get("source_class"),
            "lead_class": item.get("lead_class"),
            "source_trust": source_cfg.get("trust"),
            "source_phase_prior": source_phase_prior,
            "allowed_layers_from_source": allowed_layers,
            "headline": item.get("title"),
            "url": item.get("url"),
            "buffer_doc_id": item.get("buffer_doc_id"),
            "asset": asset,
            "layer": layer,
            "asset_confidence": asset_confidence,
            "cross_layer": bool(item.get("cross_layer")),
            "headline_phase": headline_phase,
            "final_phase": final_phase,
            "materiality_score": materiality_score,
            "materiality_family": materiality_family,
            "verdict": verdict,
            "chief_called": chief_called,
            "escalation_gate": escalation_gate,
            "skipped": skipped,
            "note": note,
            "veto_flags": veto_flags,
            "no_edge_flags": no_edge_flags,
            "asset_class": item.get("asset_class"),
            "trigger_role": item.get("trigger_role"),
            "requires_context": item.get("requires_context"),
            "context_found": context_found,
            "context_missing": context_missing,
            "identity_reason": item.get("identity_reason"),
        }
    )


# ── одна карточка (async — LLM + Telegram) ───────────────────────────────────
def audit_bucket_for_phase(phase: str | None, event_type: str | None) -> str:
    p = str(phase or "").upper()
    et = str(event_type or "").lower()
    if p in {"FUTURE", "EXPECTED"}:
        return "future_pending"
    if p == "LEADING":
        return "leading_seen"
    if et == "market_mover":
        return "market_mover"
    if p in {"REALIZED", "COINCIDENT"}:
        return "realized_seen"
    return "other_seen"


def is_channel_calendar_digest(item: dict, source: str) -> bool:
    text = f"{item.get('title') or ''}\n{item.get('text') or ''}".lower()
    calendar_markers = (
        "календарь",
        "calendar",
        "рљрђр›р•рќр”рђр",  # mojibake for "КАЛЕНДАР..."
        "рєр°р»рµрѕрґр°р",  # alternate mojibake/lowercase rendering
    )
    if source == "tg_markettwits" and any(marker in text for marker in calendar_markers):
        return True
    return False


def write_deterministic_event_audit(
    *,
    item: dict,
    asset: str | None,
    layer: int | None,
    phase: str | None,
    event_type: str | None,
    source: str,
    source_class: str,
    lead_class: str,
    canon: str,
    source_ts: str | None,
) -> bool:
    bucket = "calendar_digest" if is_channel_calendar_digest(item, source) else audit_bucket_for_phase(phase, event_type)
    return J.write_event_audit(
        {
            "audit_bucket": bucket,
            "source": source,
            "source_class": source_class,
            "lead_class": lead_class,
            "source_url": canon,
            "source_ts": source_ts,
            "headline": item.get("title"),
            "asset": asset,
            "layer": layer,
            "okx_inst": item.get("okx_inst"),
            "event_type": event_type,
            "event_phase": phase,
            "event_key": item.get("event_key"),
            "market_tape_trigger": item.get("market_tape_trigger"),
            "move_1h_pct": item.get("move_1h_pct"),
            "move_24h_pct": item.get("move_24h_pct"),
            "llm_called": False,
        }
    )


def upsert_future_pending_without_llm(
    *,
    item: dict,
    asset: str | None,
    layer: int | None,
    phase: str | None,
    event_type: str | None,
    source: str,
    canon: str,
    source_ts: str | None,
) -> tuple[str, bool]:
    expected_ts = item.get("expected_start_ts") or item.get("event_time") or source_ts or J.now_iso()
    rec = PS.build_pending_record(
        asset=asset,
        layer=layer,
        event_type=event_type or "unknown",
        expected_start_ts=expected_ts,
        expected_end_ts=item.get("expected_end_ts") or expected_ts,
        kind=PS.KIND_INFERRED,
        source_id=source,
        source_ref=canon,
        expected_direction=item.get("bias_hint") or item.get("expected_direction") or "unknown",
        expectation_text=item.get("text") or item.get("title") or "",
        confidence=item.get("identity_confidence"),
        created_from_event_key=item.get("event_key"),
        metadata={
            "headline": item.get("title"),
            "phase": phase,
            "lead_class": item.get("lead_class"),
            "trigger_type": item.get("trigger_type"),
            "audit_bucket": "future_pending",
        },
    )
    return PS.upsert_pending(rec)


async def process_item(item: dict, mline: str | None, dry: bool,
                       btc_ref: float | None = None,
                       recent: list | None = None, dedup_min: int = 88,
                       carded: dict | None = None, max_per_asset: int = 1,
                       use_buffer: bool = False,
                       send_telegram: bool = True) -> dict | None:
    headline = item["title"]
    url = item.get("url")
    doc_id = item.get("buffer_doc_id")
    normalized = bool(item.get("buffer_doc_id"))
    pre_routed = bool(item.get("asset")) and not normalized  # листинг = актив/слой известны из инструмента
    lead_class = item.get("lead_class", "LAGGING")
    source_class = item.get("source_class", "rss")
    source = item.get("source", "cointelegraph")
    source_cfg = source_meta(source)
    context_status = None  # Phase 5: tracks no-buffer / source-is-confirmation status
    allowed = list(source_cfg.get("layers") or [])
    source_phase_prior = str(source_cfg.get("phase_prior") or "mixed")
    headline_temporal = route_temporal(headline)
    headline_phase = str(headline_temporal.get("phase") or "AMBIGUOUS")
    cross_layer = False   # сильный алиас восстановил актив вне слоя источника (recall-fix, аудит)

    # 1) РОУТЕР актив/слой (+ материальность для RSS; листинг pre-routed, материален by design)
    if normalized:
        if not item.get("asset"):
            write_routing_snapshot(
                item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
                asset=None, layer=item.get("layer"), asset_confidence=None,
                source_phase_prior=source_phase_prior, headline_phase=headline_phase,
                final_phase=None, materiality_score=item.get("materiality_score"),
                materiality_family=item.get("event_type"), skipped="no_tracked_asset",
                note="normalized_item_without_asset",
            )
            return {"skipped": "no_tracked_asset", "headline": headline}
        asset, inst = item["asset"], item.get("okx_inst")
        layer, conf = int(item.get("layer") or 2), float(item.get("asset_confidence") or 1.0)
        baseline_sym = item.get("baseline")
        cross_layer = bool(item.get("cross_layer"))   # буфер-путь: флаг из normalize_pending (recall-fix аудит)
        mat = {"family": item.get("event_type") or "unclassified",
               "score": item.get("materiality_score") or 0.0}
        phase = str(item.get("phase") or "AMBIGUOUS").upper()
    elif pre_routed:
        asset, inst = item["asset"], item.get("okx_inst")
        layer, conf = int(item.get("layer", 2)), 1.0
        baseline_sym = item.get("baseline")
        mat = {"family": item.get("event_type", "unclassified"), "score": 0.6}
        # Temporal classification: use source-aware analysis instead of hardcoding
        src_kind = item.get("channel_kind") or item.get("trigger_type", "")
        temp = classify_temporal(
            text=item.get("text") or headline,
            source_kind=src_kind,
            source_ts=item.get("time"),
            event_type=item.get("event_type", ""),
            phase_prior=source_phase_prior,
        )
        phase = temp["phase"]
        if temp.get("is_stale"):
            write_routing_snapshot(
                item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
                asset=asset, layer=layer, asset_confidence=conf,
                source_phase_prior=source_phase_prior, headline_phase=headline_phase,
                final_phase=phase, materiality_score=mat.get("score"),
                materiality_family=mat.get("family"), skipped="stale_article",
                note=temp.get("temporal_reason", ""),
            )
            return {"skipped": "stale_article", "headline": headline, "asset": asset}
    else:
        allowed_set = set(allowed) or None
        routed = route_asset(headline, allowed_layers=allowed_set)   # слои источника ограничивают кандидатов
        if not routed:
            write_routing_snapshot(
                item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
                asset=None, layer=None, asset_confidence=None,
                source_phase_prior=source_phase_prior, headline_phase=headline_phase,
                final_phase=None, materiality_score=None, materiality_family=None,
                skipped="no_tracked_asset", note="route_asset_returned_none",
            )
            return {"skipped": "no_tracked_asset", "headline": headline}
        asset, inst, layer = routed["asset"], routed["okx_inst"], routed["layer"]
        conf, baseline_sym = routed["confidence"], routed.get("baseline")
        cross_layer = bool(routed.get("cross_layer"))   # strong-match минул гейт слоёв источника
        item["cross_layer"] = cross_layer               # → routing audit (write_routing_snapshot)
        # V0: режем ТОЛЬКО заведомый шум (noise_genre); no_material_term пропускаем в LLM
        # (рой: не резать сюрприз вслепую пока журнал мал; V1 ужесточит).
        mat = score_materiality(headline, layer)
        if mat.get("drop_reason") == "noise_genre":
            write_routing_snapshot(
                item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
                asset=asset, layer=layer, asset_confidence=conf,
                source_phase_prior=source_phase_prior, headline_phase=headline_phase,
                final_phase=None, materiality_score=mat.get("score"),
                materiality_family=mat.get("family"), skipped="noise_genre",
                note=str(mat.get("matched") or ""),
            )
            return {"skipped": "noise_genre", "headline": headline, "asset": asset}
        phase = route_temporal(headline)["phase"]

    # CONTEXT (ценовой обзор/мнение/прогноз) — не событие → не будим LLM (экономия токенов)
    if phase == "CONTEXT":
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=asset, layer=layer, asset_confidence=conf,
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=phase, materiality_score=mat.get("score"),
            materiality_family=mat.get("family"), skipped="context_commentary",
        )
        return {"skipped": "context_commentary", "headline": headline, "asset": asset}
    # КАП — не флудить одним активом за проход (был кейс 4 BTC NO_GO подряд)
    if carded is not None and carded.get(asset, 0) >= max_per_asset:
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=asset, layer=layer, asset_confidence=conf,
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=phase, materiality_score=mat.get("score"),
            materiality_family=mat.get("family"), skipped="asset_capped",
        )
        return {"skipped": "asset_capped", "headline": headline, "asset": asset}

    # EVENT-ДЕДУП — то же событие из N лент/проходов → 1 карточка
    if recent is not None and is_duplicate(headline, asset, recent, dedup_min):
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=asset, layer=layer, asset_confidence=conf,
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=phase, materiality_score=mat.get("score"),
            materiality_family=mat.get("family"), skipped="dup_event",
        )
        return {"skipped": "dup_event", "headline": headline, "asset": asset}
    canon = canonical_url(url or f"https://www.okx.com/trade-swap/{(inst or asset or '').lower()}")
    ekey = item.get("event_key") or make_event_key(asset, headline)

    # 3) тело — для normalised/pre-routed items берем structured text, для RSS/статей — extract.
    if normalized:
        body_text = (item.get("text") or "").strip()
        source_ts = item.get("time") or J.now_iso()[:10]
        try:
            low_conf = float(item.get("extraction_quality") or 0.0) < 0.5
        except (TypeError, ValueError):
            low_conf = True
    elif pre_routed:
        body_text = (item.get("text") or "").strip()
        source_ts = item.get("time") or J.now_iso()[:10]
        low_conf = not bool(body_text)
    elif url and not pre_routed:
        fetch_url = url
        if not dry and GN.is_google_news_url(url):   # google-обёртка → реальный URL статьи
            fetch_url = GN.decode_google_news_url(url) or GN.resolve_google_news_url(url) or url
        ext = extract(fetch_url) if not dry else {"text": "(dry-run)", "date": item.get("time")}
        if not ext or ext.get("error") or len(ext.get("text") or "") < 200:
            low_conf, body_text = True, ""
            source_ts = (ext or {}).get("date") or item.get("time") or J.now_iso()[:10]
        else:
            low_conf, body_text = False, ext["text"]
            source_ts = ext.get("date") or item.get("time") or J.now_iso()[:10]
    else:
        low_conf, body_text = True, ""
        source_ts = item.get("time") or J.now_iso()[:10]

    if is_stale_story(source_ts, source, lead_class, source_class):
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=asset, layer=layer, asset_confidence=conf,
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=phase, materiality_score=mat.get("score"),
            materiality_family=mat.get("family"), skipped="stale_article",
        )
        return {"skipped": "stale_article", "headline": headline, "asset": asset}

    event_type_for_audit = item.get("event_type") or mat.get("family") or "unclassified"
    if not dry:
        write_deterministic_event_audit(
            item=item,
            asset=asset,
            layer=layer,
            phase=phase,
            event_type=event_type_for_audit,
            source=source,
            source_class=source_class,
            lead_class=lead_class,
            canon=canon,
            source_ts=source_ts,
        )
    if str(event_type_for_audit or "").lower() == "market_mover":
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=asset, layer=layer, asset_confidence=conf,
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=phase, materiality_score=mat.get("score"),
            materiality_family=event_type_for_audit, skipped="market_mover_audit_only",
            note="recorded_to_event_audit_without_llm",
        )
        return {
            "handled": "market_mover_audit_only",
            "headline": headline,
            "asset": asset,
            "tokens": 0,
            "cost_rub": 0.0,
        }
    if is_channel_calendar_digest(item, source):
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=asset, layer=layer, asset_confidence=conf,
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=phase, materiality_score=mat.get("score"),
            materiality_family=event_type_for_audit, skipped="calendar_digest_audit_only",
            note="channel digest recorded to event_audit without llm",
        )
        return {
            "handled": "calendar_digest_audit_only",
            "headline": headline,
            "asset": asset,
            "tokens": 0,
            "cost_rub": 0.0,
        }
    if str(phase or "").upper() in {"FUTURE", "EXPECTED"}:
        if not dry:
            pending_id, changed = upsert_future_pending_without_llm(
                item=item,
                asset=asset,
                layer=layer,
                phase=phase,
                event_type=event_type_for_audit,
                source=source,
                canon=canon,
                source_ts=source_ts,
            )
            write_routing_snapshot(
                item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
                asset=asset, layer=layer, asset_confidence=conf,
                source_phase_prior=source_phase_prior, headline_phase=headline_phase,
                final_phase=phase, materiality_score=mat.get("score"),
                materiality_family=event_type_for_audit, skipped="future_pending_no_llm",
                note=f"pending_id={pending_id};changed={changed}",
            )
        return {
            "handled": "future_pending_no_llm",
            "headline": headline,
            "asset": asset,
            "tokens": 0,
            "cost_rub": 0.0,
        }

    # baseline-цена ПО СЛОЮ (excess vs index): BTC из btc_ref, иначе снять (None = manual, off-OKX)
    if baseline_sym == "BTC-USDT-SWAP":
        baseline_price = btc_ref
    elif baseline_sym and not dry:
        baseline_price = okx_last(baseline_sym)
    else:
        baseline_price = None

    price, price_reason = (okx_last_with_reason(inst) if (not dry and inst) else (None, "no_instrument"))
    news = {"headline": headline, "text": body_text, "date": source_ts, "url": url or canon}

    # Inject identity metadata for LLM framing (asset_class, trigger_role, etc.)
    if item.get("asset_class"):
        news["asset_class"] = item["asset_class"]
    if item.get("trigger_role"):
        news["trigger_role"] = item["trigger_role"]
    if item.get("identity_reason"):
        news["identity_reason"] = item["identity_reason"]
    if item.get("identity_confidence") is not None:
        news["identity_confidence"] = item["identity_confidence"]
    if item.get("flow_context"):
        news["flow_context"] = item["flow_context"]
    if item.get("channel_kind"):
        news["channel_kind"] = item["channel_kind"]

    # L1-обогащение: строка ETF-потоков в рыночный фон (контекст для cheap/chief,
    # НЕ сигнал и НЕ гейт — пусто, если SCANNER_ETF_FLOW_PROVIDER не сконфигурирован)
    ctx_line = mline
    if int(layer or 0) == 1 and not dry:
        etf_line = l1_context_line()
        if etf_line:
            ctx_line = f"{mline} | {etf_line}" if mline else etf_line

    # Context building for items that need it (requires_context=True from identity resolver)
    requires_context = bool(item.get("requires_context"))
    trigger_context_pkg = None
    context_status = None
    if requires_context and not dry:
        if not use_buffer:
            # No buffer: context search is unreliable — mark as not available
            context_status = "not_available_no_buffer"
            # Official/leading sources are self-confirming — allow through
            is_official = source_cfg.get("trust") in ("official", "primary")
            is_leading = lead_class == "LEADING"
            if is_official or is_leading:
                # Source itself is confirmation — skip context gate
                context_status = "source_is_confirmation"
            elif item.get("asset_class") in ("tokenized_equity", "pre_ipo_equity"):
                # Equity/tokenized without buffer: conservative drop
                write_routing_snapshot(
                    item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
                    asset=asset, layer=layer, asset_confidence=conf,
                    source_phase_prior=source_phase_prior, headline_phase=headline_phase,
                    final_phase=phase, materiality_score=mat.get("score"),
                    materiality_family=mat.get("family"), skipped="needs_context_no_buffer",
                    note=f"identity:{item.get('identity_reason','')} no_buffer_for_equity",
                    context_found=False,
                    context_missing=["no_buffer_available"],
                )
                return {"skipped": "needs_context_no_buffer", "headline": headline, "asset": asset}
            # Other assets without buffer: allow through with context_status note
        else:
            trigger_context_pkg = TC.build_context(
                symbol=asset,
                text=(item.get("text") or headline)[:500],
                asset_class=item.get("asset_class", "unknown"),
                source_id=source,
                flow_context=item.get("flow_context"),
            )
            if not trigger_context_pkg.get("context_found"):
                missing = trigger_context_pkg.get("context_missing", [])
                write_routing_snapshot(
                    item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
                    asset=asset, layer=layer, asset_confidence=conf,
                    source_phase_prior=source_phase_prior, headline_phase=headline_phase,
                    final_phase=phase, materiality_score=mat.get("score"),
                    materiality_family=mat.get("family"), skipped="needs_context",
                    note=f"identity:{item.get('identity_reason','')} missing:{','.join(missing[:3])}",
                    context_found=False,
                    context_missing=missing,
                )
                return {"skipped": "needs_context", "headline": headline, "asset": asset,
                        "trigger_context": trigger_context_pkg}
            else:
                context_status = "context_found"
                ctx_summary = trigger_context_pkg.get("context_summary", "")
                if ctx_summary:
                    news["context_package"] = ctx_summary

    # Phase 5: inject context_status AFTER context-building (was injected before, always None)
    if context_status:
        news["context_status"] = context_status
    # For liquidation flow: build lightweight flow context package for audit even if requires_context=False
    if item.get("asset_class") == "liquidation_flow" and not trigger_context_pkg and not dry:
        trigger_context_pkg = TC.build_context(
            symbol=asset,
            text=(item.get("text") or headline)[:500],
            asset_class="liquidation_flow",
            source_id=source,
            flow_context=item.get("flow_context"),
        )
        context_status = "flow_context_built"

    # 3) ОРКЕСТРАТОР: дешёвый слой-агент → кодовый гейт → chief (только кандидаты). dry = заглушка без LLM.
    if dry:
        orch = {"decision": "journal", "verdict": "NO_GO", "side": "none", "chief_called": False,
                "agent": {"direction": "none", "confidence": None, "phase": phase, "asset": asset,
                          "event_type": mat.get("family") or "unknown", "materiality": mat.get("score"),
                          "red_flags": [], "mechanics": [], "key_facts": []},
                "chief": None, "usage": [], "send_channel": True,
                "pre_verdict": "JOURNAL_NO_GO", "should_escalate": False,
                "escalation_gate": "CHEAP_NO_GO", "escalation_reason": "dry-run"}
    else:
        orch = await orchestrator.process(news, asset, layer, lead_class, price, ctx_line,
                                          source=source, source_class=source_class,
                                          source_trust=source_cfg.get("trust"),
                                          low_confidence=low_conf)

    if orch.get("decision") == "trash":
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=asset, layer=layer, asset_confidence=conf,
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=str((orch.get("agent") or {}).get("phase") or phase),
            materiality_score=((orch.get("agent") or {}).get("materiality")
                               if (orch.get("agent") or {}).get("materiality") is not None
                               else mat.get("score")),
            materiality_family=(orch.get("agent") or {}).get("event_type") or mat.get("family"),
            skipped="trash_lowmat",
        )
        return {"skipped": "trash_lowmat", "headline": headline, "asset": asset}

    agent = orch.get("agent") or {}
    ch = orch.get("chief") or {}
    verdict, side = orch.get("verdict", "NO_GO"), orch.get("side", "none")

    # ПРЕДОХРАНИТЕЛЬ (в КОДЕ): GO+сторона только если LEADING (анти-мираж запаздывающего эджа)
    if verdict == "GO" and side != "none" and lead_class != "LEADING":
        verdict = "WATCH"

    usage_rows = orch.get("usage") or []
    tokens = sum(int(u.get("total_tokens") or 0) for u in usage_rows)
    cost_rub = round(sum(float(u.get("cost_rub") or 0.0) for u in usage_rows), 4)

    if orch.get("llm_error") and not dry:
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=asset, layer=layer, asset_confidence=conf,
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=str(agent.get("phase") or phase),
            materiality_score=(agent.get("materiality")
                               if agent.get("materiality") is not None else mat.get("score")),
            materiality_family=agent.get("event_type") or mat.get("family"),
            escalation_gate=orch.get("escalation_gate"), skipped=orch.get("retry_status") or "llm_error",
            veto_flags=agent.get("veto_flags"), no_edge_flags=agent.get("no_edge_flags"),
        )
        print(f"  · LLM budget/access interrupted - retry next pass: {headline[:60]}")
        return {"skipped": "llm_failed", "headline": headline, "asset": asset,
                "tokens": tokens, "cost_rub": cost_rub}

    # chief упал на эскалированном кандидате → НЕ финализируем обычным NO_GO: возвращаем
    # событие в очередь (buffer→READY / RSS→не-seen, существующий llm_failed-путь);
    # после CHIEF_RETRY_MAX ретраев финализируем с гейтом CHIEF_UNAVAILABLE (видно в аудитах).
    if orch.get("chief_error") and not dry:
        if chief_retry_decision(doc_id or canon) == "retry":
            write_routing_snapshot(
                item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
                asset=asset, layer=layer, asset_confidence=conf,
                source_phase_prior=source_phase_prior, headline_phase=headline_phase,
                final_phase=str(agent.get("phase") or phase),
                materiality_score=(agent.get("materiality")
                                   if agent.get("materiality") is not None else mat.get("score")),
                materiality_family=agent.get("event_type") or mat.get("family"),
                escalation_gate="CHIEF_ERROR_PENDING", skipped="chief_error_retry",
                veto_flags=agent.get("veto_flags"), no_edge_flags=agent.get("no_edge_flags"),
            )
            print(f"  · chief недоступен — ретрай следующим проходом: {headline[:60]}")
            return {"skipped": "llm_failed", "headline": headline, "asset": asset,
                    "tokens": tokens, "cost_rub": cost_rub}
        orch["escalation_gate"] = "CHIEF_UNAVAILABLE"
        orch["retry_status"] = "chief_unavailable"

    last_usage = (ch.get("_usage") or (orch.get("usage") or [{}])[-1] or {})
    fields = {
        "horizon_hours": ch.get("horizon_hours", 48), "levels": ch.get("levels"),
        "catalyst": "; ".join(map(str, agent.get("key_facts") or []))[:200],
        "in_price": ch.get("in_price", ""),
        # red_flag журнала = ТОЛЬКО veto (риск); слабости (no_edge) — в reasoning-логе
        "red_flag": ", ".join(map(str, (agent.get("veto_flags") if "veto_flags" in agent
                                        else agent.get("red_flags")) or [])) or "none",
        "mechanics": ", ".join(map(str, agent.get("mechanics") or [])) or "none",
        "surprise": ch.get("surprise") or agent.get("phase", ""),
        "asymmetry": ch.get("asymmetry", ""), "invalidation": ch.get("invalidation", ""),
        "forecast": ch.get("forecast", ""),
        "summary": (ch.get("summary") or agent.get("no_go_reason") or orch.get("escalation_reason")
                    or "дешёвый фильтр: chief не звали — в журнал (датасет)"),
    }

    # 3b) OKX instrument resolution + data readiness (WATCH/GO only — paper farm gate).
    # Verifies the instrument really trades on OKX (no more guessed GEOD-USDT-SWAP) and
    # picks a timeframe by data, not habit. Off by default; never an order path.
    farm_fields: dict = {}
    if not dry and verdict in ("WATCH", "GO") and farm_resolve_enabled():
        try:
            farm_fields = enrich_trigger(item, agent.get("asset") or asset, inst, body_text, use_buffer)
        except Exception as exc:
            print(f"  farm-resolve: {exc}")
            farm_fields = {}

    # 4) строка журнала
    row = J.build_row(
        source_url=canon, source_ts=source_ts, layer=layer,
        asset=agent.get("asset") or asset,
        trigger_type=item.get("trigger_type") or ("okx_listing" if pre_routed else TRIGGER), headline=headline,
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
        escalation_gate=orch.get("escalation_gate"),
        agent_direction=agent.get("direction", "none"), agent_confidence=agent.get("confidence"),
        llm_provider=last_usage.get("provider"), llm_model=last_usage.get("model"),
        source_trust=source_cfg.get("trust"),
        source_phase_prior=source_phase_prior,
        headline_phase=headline_phase,
        allowed_layers_from_source=allowed,
        cross_layer=cross_layer,
        levels=fields["levels"],
        catalyst=fields["catalyst"], in_price=fields["in_price"],
        red_flag=fields["red_flag"], mechanics=fields["mechanics"],
        surprise=fields["surprise"], side=side,
        asymmetry=fields["asymmetry"], invalidation=fields["invalidation"],
        forecast=fields["forecast"], summary=fields["summary"],
        low_confidence=low_conf,
        outcome_source=("okx" if price is not None else "manual"),
        price_reason=price_reason,
        # Phase 4: identity/context fields
        asset_class=item.get("asset_class"),
        trigger_role=item.get("trigger_role"),
        channel_kind=item.get("channel_kind"),
        context_found=trigger_context_pkg.get("context_found") if trigger_context_pkg else None,
        context_missing=trigger_context_pkg.get("context_missing") if trigger_context_pkg else None,
        identity_reason=item.get("identity_reason"),
        identity_confidence=item.get("identity_confidence"),
        temporal_phase=temp.get("phase") if (pre_routed and temp) else None,
        temporal_reason=temp.get("temporal_reason") if (pre_routed and temp) else None,
        **farm_fields,   # okx_resolved/inst_type/asset_class/readiness/selected_tf/farm_eligible (WATCH/GO)
    )
    cid = ("DRY-" + J.card_id_for(canon)) if dry else J.write_row(row)
    if cid and not dry:
        extraction_meta = {
            "quality": item.get("extraction_quality"),
            "status": item.get("extraction_status"),
            "method": item.get("extraction_method"),
        }
        R.write_event_block(
            R.build_event_block(
                row=row,
                source_item=item,
                news=news,
                market_ctx=mline,
                buffer_doc_id=doc_id,
                extraction_meta=extraction_meta,
            )
        )
        R.write_reasoning_block(
            R.build_reasoning_block(
                row=row,
                agent=agent,
                orchestrator=orch,
                chief=ch or None,
                usage=usage_rows,
                buffer_doc_id=doc_id,
            )
        )
        pending = PS.build_pending_from_journal(row)
        if pending:
            PS.upsert_pending(pending)
        else:
            matched = PS.match_realized_event(row)
            if matched:
                PS.mark_matched(str(matched.get("pending_id")), row["card_id"])
        try:
            WQ.upsert_watch(row)
        except Exception as exc:
            print(f"  watch_queue: {exc}")
        write_routing_snapshot(
            item=item, source=source, source_cfg=source_cfg, allowed_layers=allowed,
            asset=row.get("asset"), layer=row.get("layer"), asset_confidence=row.get("asset_confidence"),
            source_phase_prior=source_phase_prior, headline_phase=headline_phase,
            final_phase=str(row.get("event_phase") or phase),
            materiality_score=row.get("materiality_score"),
            materiality_family=row.get("event_type"),
            verdict=row.get("verdict"), chief_called=row.get("chief_called"),
            escalation_gate=orch.get("escalation_gate"),
            note=row.get("side"),
            veto_flags=agent.get("veto_flags"), no_edge_flags=agent.get("no_edge_flags"),
        )
    card = format_card(row)

    # 5+6) график + доставка — только GO/WATCH chief-карточки (should_send_to_channel);
    # NO_GO (chief или дешёвый) = журнал/датасет, в канал не идёт (SCANNER_SEND_NO_GO=true вернёт)
    sent = None
    send_allowed = (
        send_telegram
        and bool(cid)
        and should_send_to_channel(verdict, bool(orch.get("send_channel")))
    )
    telegram_targets = _scanner_chat_ids()
    if not send_allowed:
        write_telegram_delivery({
            "card_id": cid, "asset": asset, "verdict": verdict, "source": source,
            "send_channel": bool(orch.get("send_channel")), "dry": dry,
            "status": "skipped",
            "reason": "delivery_disabled" if not send_telegram else "send_gate",
        })
    elif not telegram_targets:
        write_telegram_delivery({
            "card_id": cid, "asset": asset, "verdict": verdict, "source": source,
            "send_channel": bool(orch.get("send_channel")), "dry": dry,
            "status": "skipped", "reason": "missing_notification_chat_id",
        })
    elif dry:
        write_telegram_delivery({
            "card_id": cid, "asset": asset, "verdict": verdict, "source": source,
            "send_channel": bool(orch.get("send_channel")), "dry": dry,
            "status": "skipped", "reason": "dry_run",
        })
    else:
        chart_path = make_chart(inst, J.now_iso(), row) if inst else None
        try:
            sent_count = 0
            for telegram_target in telegram_targets:
                photo_message_id = None
                if chart_path:
                    photo_message_id = await send_photo_to(
                        telegram_target,
                        chart_path,
                        caption=format_caption(row),
                        parse_mode="HTML",
                    )
                message_id = await send_message_to(telegram_target, card)
                delivery_status = "sent" if message_id is not None else "skipped"
                delivery_reason = "photo_message" if chart_path else "message"
                if message_id is None:
                    delivery_reason = "telegram_token_not_configured"
                if chart_path and photo_message_id is None and message_id is not None:
                    delivery_status = "partial"
                    delivery_reason = "photo_message_id_missing"
                write_telegram_delivery({
                    "card_id": cid, "asset": asset, "verdict": verdict, "source": source,
                    "send_channel": bool(orch.get("send_channel")), "dry": dry,
                    "status": delivery_status, "reason": delivery_reason, "message_id": message_id,
                })
                if delivery_status in {"sent", "partial"}:
                    sent_count += 1
            sent = f"{'photo+' if chart_path else ''}message:{sent_count}/{len(telegram_targets)}"
        except Exception as e:
            print(f"  telegram: {e}")
            write_telegram_delivery({
                "card_id": cid, "asset": asset, "verdict": verdict, "source": source,
                "send_channel": bool(orch.get("send_channel")), "dry": dry,
                "status": "error", "reason": "exception", "error": str(e)[:300],
            })

    return {"card_id": cid, "row": row, "card": card, "sent": sent,
            "asset": asset, "price": price, "canon": canon, "tokens": tokens,
            "cost_rub": cost_rub,
            "chief_called": orch.get("chief_called"), "channel": bool(orch.get("send_channel"))}


# ── оркестрация (одиночный проход) ───────────────────────────────────────────
async def run(
    limit: int,
    dry: bool,
    use_buffer: bool = False,
    *,
    max_pass_seconds: float | None = None,
    resolve_max_seconds: float | None = None,
    monotonic=time.monotonic,
    should_stop=None,
) -> None:
    # `run` is also a callable integration surface; do not rely on `main` to
    # reject an unsafe root before journals, queues, or scanner state are used.
    _configure_runtime_paths(_product_root())
    pass_started = monotonic()
    bounded_pass_seconds = max_pass_seconds
    if bounded_pass_seconds is not None:
        bounded_pass_seconds = float(bounded_pass_seconds)
        if not math.isfinite(bounded_pass_seconds):
            raise ValueError("max_pass_seconds must be finite")
        bounded_pass_seconds = max(
            1.0, min(bounded_pass_seconds, MAX_CANONICAL_PASS_SECONDS)
        )
    pass_deadline = (
        pass_started + bounded_pass_seconds
        if bounded_pass_seconds is not None
        else None
    )
    bounded_resolve_seconds = resolve_max_seconds
    if bounded_resolve_seconds is not None:
        bounded_resolve_seconds = float(bounded_resolve_seconds)
        if not math.isfinite(bounded_resolve_seconds):
            raise ValueError("resolve_max_seconds must be finite")
        bounded_resolve_seconds = max(
            1.0, min(bounded_resolve_seconds, MAX_RESOLVE_SECONDS)
        )
    stop_requested = should_stop or _scanner_stop_requested
    completed_chunks = 0
    budget_exhausted = False
    resolver_deferred = 0
    LBG.reset_session()
    J.ensure_pending_store()
    if not dry:
        PS.expire_old()
    okx_day_test = env_enabled("SCANNER_OKX_DAY_TEST")
    okx_only = env_enabled("SCANNER_OKX_ONLY")
    send_telegram = scanner_telegram_enabled()
    seen = load_seen()
    mline = market_ctx_line()
    btc_ref = okx_last("BTC-USDT-SWAP") if not dry else None   # якорь baseline на проход
    dcfg = dedup_config()
    dedup_min = int(dcfg.get("fuzzy_min", 88))
    recent = J.recent_events(int(dcfg.get("window_hours", 48)))   # окно event-дедупа
    max_per_asset = int(limits_config().get("max_cards_per_asset_per_run", 1))
    provider_failures = 0
    carded: dict = {}                                            # кап карточек на актив за проход
    print(f"=== scanner_v0 {'(DRY-RUN)' if dry else ''} | {'BUFFER' if use_buffer else 'RSS+листинги'} | "
          f"{'OKX_DAY_TEST' if okx_day_test else 'NORMAL'} | "
          f"telegram={'ON targets='+str(len(_scanner_chat_ids())) if (_scanner_chat_ids() and not dry) else 'OFF (dry-доставка)'} ===")
    print(f"рыночный фон: {mline or '—'}")

    try:
        rss_items = [] if okx_only else fetch_rss()
    except Exception as e:
        print(f"RSS ОШИБКА: {e}")
        provider_failures += 1
        rss_items = []
    try:
        listings = [] if dry else fetch_new_listings(within_hours=24, limit=5)
    except Exception as e:
        print(f"OKX listings ОШИБКА: {e}")
        provider_failures += 1
        listings = []
    try:
        ann_enabled = bool(source_meta("okx_announcements").get("enabled")) or okx_day_test
        announcement_items = [] if (dry or not ann_enabled) else fetch_okx_announcements(limit=12)
    except Exception as e:
        print(f"OKX announcements ОШИБКА: {e}")
        provider_failures += 1
        announcement_items = []
    try:
        tape_enabled = bool(source_meta("okx_market_tape").get("enabled")) or okx_day_test
        market_tape_items = [] if (dry or not tape_enabled) else fetch_okx_market_tape(limit=12)
    except Exception as e:
        print(f"OKX market tape ОШИБКА: {e}")
        provider_failures += 1
        market_tape_items = []
    try:
        sec_items = [] if (dry or okx_only) else fetch_recent_filings(within_hours=24, limit=8)
    except Exception as e:
        print(f"SEC filings ОШИБКА: {e}")
        provider_failures += 1
        sec_items = []
    try:
        tactical_items = [] if (dry or not source_meta("btc_eth_tactical").get("enabled")) else fetch_btc_eth_tactical(limit=4)
    except Exception as e:
        print(f"BTC/ETH tactical ОШИБКА: {e}")
        provider_failures += 1
        tactical_items = []
    try:
        fred_items = [] if (dry or okx_only or not source_meta("fred_calendar").get("enabled")) else fetch_fred_calendar(limit=8)
    except Exception as e:
        print(f"FRED calendar ОШИБКА: {e}")
        provider_failures += 1
        fred_items = []
    try:
        eia_items = [] if (dry or okx_only or not source_meta("eia").get("enabled")) else fetch_eia_schedule(limit=2)
    except Exception as e:
        print(f"EIA schedule ОШИБКА: {e}")
        provider_failures += 1
        eia_items = []
    try:
        opec_items = [] if (dry or okx_only or not source_meta("opec").get("enabled")) else fetch_opec_schedule(limit=2)
    except Exception as e:
        print(f"OPEC schedule ОШИБКА: {e}")
        provider_failures += 1
        opec_items = []
    try:
        earnings_items = [] if (dry or okx_only or not source_meta("earnings_calendar").get("enabled")) else fetch_earnings_calendar(limit=8)
    except Exception as e:
        print(f"Earnings calendar ОШИБКА: {e}")
        provider_failures += 1
        earnings_items = []
    try:
        unlock_items = [] if (dry or okx_only or not source_meta("token_unlocks").get("enabled")) else fetch_upcoming_unlocks(limit=12)
    except Exception as e:
        print(f"Token unlocks ОШИБКА: {e}")
        provider_failures += 1
        unlock_items = []
    try:
        dex_items = [] if (dry or okx_only or not source_meta("dexscreener").get("enabled")) else fetch_alt_flow_signals(limit=8)
    except Exception as e:
        print(f"Dexscreener ОШИБКА: {e}")
        provider_failures += 1
        dex_items = []
    try:
        risk_items = [] if (dry or okx_only or not source_meta("goplus_rugcheck").get("enabled")) else fetch_token_risk_signals(dex_items, limit=6)
    except Exception as e:
        print(f"GoPlus rugcheck ОШИБКА: {e}")
        provider_failures += 1
        risk_items = []
    try:
        telegram_items = [] if okx_only else fetch_telegram_web_sources(limit_per_source=20)
    except Exception as e:
        print(f"Telegram web ОШИБКА: {e}")
        provider_failures += 1
        telegram_items = []
    leading = listings + announcement_items + sec_items + unlock_items + tactical_items + fred_items + eia_items + opec_items + earnings_items   # опережающие/прямые сигналы
    native = market_tape_items + dex_items        # native event/market feeds (COINCIDENT)
    items = leading + risk_items + native + telegram_items + rss_items          # risk/official first, then TG/native/RSS
    if not dry:
        completed_chunks += 1
        _publish_scanner_progress(
            stage="sources_completed",
            completed_chunks=completed_chunks,
            inputs=len(items),
            provider_failures=provider_failures,
        )
    if not dry and items:                         # полный аудит: лог КАЖДОГО входящего до фильтров
        J.write_ingest([{"canon": canonical_url(it.get("url") or ""), "source": it.get("source", "?"),
                         "headline": it.get("title"), "url": it.get("url")} for it in items])
    if use_buffer and not dry:
        ing = NB.ingest_items(items)
        buffer_batch = max(limit * 4, 10)
        remaining_pass = (
            None if pass_deadline is None else max(0.0, pass_deadline - monotonic())
        )
        resolve_budget = bounded_resolve_seconds
        if remaining_pass is not None:
            resolve_budget = (
                remaining_pass
                if resolve_budget is None
                else min(resolve_budget, remaining_pass)
            )

        def resolver_progress(progress: dict) -> None:
            nonlocal completed_chunks
            completed_chunks += 1
            _publish_scanner_progress(
                stage="resolver_document_completed",
                completed_chunks=completed_chunks,
                resolver_completed=int(progress.get("completed_chunks") or 0),
                resolver_selected=int(progress.get("selected") or 0),
                resolver_remaining=int(progress.get("remaining_selected") or 0),
            )

        resolved = (
            NB.resolve_pending(limit=buffer_batch)
            if max_pass_seconds is None and resolve_max_seconds is None
            else NB.resolve_pending(
                limit=buffer_batch,
                max_wall_seconds=resolve_budget,
                progress_callback=resolver_progress,
                should_stop=stop_requested,
                monotonic=monotonic,
            )
        )
        resolver_deferred = int(resolved.get("remaining_selected") or 0)
        budget_exhausted = bool(resolved.get("budget_exhausted"))
        if resolved.get("stop_requested"):
            return
        if pass_deadline is not None and monotonic() >= pass_deadline:
            budget_exhausted = True
            normalized_stats = {"ready": 0, "dropped": 0}
        else:
            normalized_stats = NB.normalize_pending(limit=buffer_batch)
            completed_chunks += 1
            _publish_scanner_progress(
                stage="normalization_completed",
                completed_chunks=completed_chunks,
                normalized_ready=int(normalized_stats.get("ready") or 0),
                normalized_dropped=int(normalized_stats.get("dropped") or 0),
            )
        fresh = NB.ready_items(limit=max(limit * 10, limit))
        print(
            f"источники: LEADING(okx_list+okx_ann+SEC+unlock+tactical+fred+eia+opec+earnings)={len(leading)} + OKX_TAPE={len(market_tape_items)} + L2_RISK={len(risk_items)} + "
            f"L2_NATIVE={len(native)} + TG_WEB={len(telegram_items)} + RSS={len(rss_items)} | "
            f"buffer insert={ing['inserted']} update={ing['updated']} "
            f"resolve={resolved['resolved']} ready+={normalized_stats['ready']} "
            f"drop+={normalized_stats['dropped']} | к агентам={len(fresh)}\n"
        )
    else:
        fresh = [it for it in items if canonical_url(it.get("url") or "") not in seen]
        print(
            f"источники: LEADING(okx_list+okx_ann+SEC+unlock+tactical+fred+eia+opec+earnings)={len(leading)} + OKX_TAPE={len(market_tape_items)} + L2_RISK={len(risk_items)} + "
            f"L2_NATIVE={len(native)} + TG_WEB={len(telegram_items)} + RSS={len(rss_items)} | "
            f"новых={len(fresh)}\n"
        )

    made = n_dropped = n_llm_fail = total_tokens = 0
    total_cost = 0.0
    for it in fresh:
        if made >= limit:
            break
        if stop_requested():
            return
        if pass_deadline is not None and monotonic() >= pass_deadline:
            budget_exhausted = True
            break
        canon = canonical_url(it.get("url") or "")
        res = await process_item(it, mline, dry, btc_ref=btc_ref,
                                 recent=recent, dedup_min=dedup_min,
                                 carded=carded, max_per_asset=max_per_asset,
                                 use_buffer=use_buffer,
                                 send_telegram=send_telegram)
        doc_id = it.get("buffer_doc_id")
        skipped = res.get("skipped") if res else None
        # retry-minimal: НЕ помечаем seen при временном сбое LLM (повторим следующий проход)
        if not use_buffer and skipped != "llm_failed":
            seen.add(canon)
        if not res:
            continue
        total_tokens += res.get("tokens", 0)
        total_cost += float(res.get("cost_rub") or 0.0)
        if skipped:
            if skipped == "llm_failed":
                n_llm_fail += 1
                if use_buffer and doc_id and not dry:
                    NB.mark_status(doc_id, NB.STATUS_READY)
            else:                            # фильтр-дроп (роутер/материальность) → анти-survivorship
                n_dropped += 1
                if not dry:
                    stage = {"no_tracked_asset": "router", "dup_event": "dedup",
                         "context_commentary": "gate", "stale_article": "gate",
                         "asset_capped": "cap", "needs_context": "context",
                         "needs_context_no_buffer": "context"}.get(skipped, "materiality")
                    J.write_drop(
                        it["url"], it["title"], skipped,
                        asset=res.get("asset"), drop_stage=stage, source=it.get("source"),
                    )
                    if use_buffer and doc_id:
                        NB.mark_status(doc_id, NB.STATUS_DROPPED, skipped)
            print(f"  · скип [{skipped}]: {res['headline'][:65]}")
            continue
        if res.get("handled"):
            if use_buffer and doc_id and not dry:
                NB.mark_status(doc_id, NB.STATUS_ANALYZED)
            print(f"  · handled [{res['handled']}]: {res['headline'][:65]}")
            continue
        made += 1
        completed_chunks += 1
        if not dry:
            _publish_scanner_progress(
                stage="card_completed",
                completed_chunks=completed_chunks,
                cards=made,
            )
        if use_buffer and doc_id and not dry:
            NB.mark_status(doc_id, NB.STATUS_ANALYZED)
        r = res["row"]
        recent.append((res.get("asset"), r["headline"]))   # within-run дубли тоже ловим
        carded[res["asset"]] = carded.get(res["asset"], 0) + 1   # кап на актив
        sent = f"sent msg_id={res['sent']}" if res.get("sent") else ("dry-доставка" if not dry else "не отправлено")
        print(f"\n[{made}] {r['verdict']} {r['side']} · {r['asset']} L{r['layer']} {r['lead_class']}"
              f" · {r['source']} · @ {res.get('price')} · {r['event_phase']} · card_id={res['card_id'] or 'DUP'} · {sent}")
        print("    " + "\n    ".join(res["card"].splitlines()))

    cost = round(total_cost if total_cost else total_tokens / 1000 * RATE_RUB_PER_1K, 2)
    if not dry:                       # dry ничего не персистит (не съедает seen-слоты)
        save_seen(seen)
        J.write_budget({"n_ingested": len(items), "n_fresh": len(fresh), "n_cards": made,
                        "n_dropped": n_dropped, "n_llm_fail": n_llm_fail,
                        "total_tokens": total_tokens, "cost_rub": cost})
        # Storage hygiene: rotate oversized append-only logs + LRU-bound the hot cache
        # (no-op until a cap is hit; never breaks the scan). Hot/cold policy, paper-only.
        try:
            from src.research_lab import storage_policy as SPOL
            SPOL.maintain([J.JOURNAL, J.INGEST, J.DROPS, J.BUDGET, J.EVENT_AUDIT, J.ROUTING_AUDIT],
                          apply=False)
        except Exception as exc:
            print(f"  storage-maintain: {exc}")
        completed_at = dt.datetime.now(tz=dt.timezone.utc).timestamp()
        product_root = _product_root()
        pass_elapsed_seconds = max(0.0, monotonic() - pass_started)
        publish_checkpoint(
            product_root,
            component="scanner",
            sequence=max(1, int(completed_at * 1_000_000)),
            status=(
                "degraded"
                if provider_failures or budget_exhausted or resolver_deferred
                else "idle"
                if not fresh
                else "completed"
            ),
            metrics=scanner_metrics(
                inputs=len(items),
                fresh=len(fresh),
                cards=made,
                dropped=n_dropped,
                llm_failures=n_llm_fail,
                provider_failures=provider_failures,
                budget_exhausted=budget_exhausted,
                resolver_deferred=resolver_deferred,
                completed_chunks=completed_chunks,
                pass_elapsed_seconds=pass_elapsed_seconds,
            ),
            completed_at=completed_at,
            rcc_run=rcc_run_identity_from_environment(),
        )
    print(f"\n=== готово: {made} карточек · seen={len(seen)}"
          f"{' (dry — не сохранён)' if dry else ''} · токенов={total_tokens} (~{cost} RUB) · журнал={J.JOURNAL} ===")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="плумбинг без LLM/Telegram")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="макс карточек за проход")
    ap.add_argument("--buffer", action="store_true", help="читать события из SQLite news_buffer после ingest/resolve/normalize")
    ap.add_argument(
        "--max-pass-seconds",
        type=float,
        default=_env_seconds(
            "SCANNER_MAX_PASS_SECONDS", 480.0, MAX_CANONICAL_PASS_SECONDS
        ),
        help="bounded wall budget for one canonical scanner pass",
    )
    ap.add_argument(
        "--resolve-max-seconds",
        type=float,
        default=_env_seconds(
            "SCANNER_RESOLVE_MAX_SECONDS", 180.0, MAX_RESOLVE_SECONDS
        ),
        help="bounded wall budget for article resolution inside one pass",
    )
    args = ap.parse_args()
    try:
        _configure_runtime_paths(_product_root())
    except ValueError as exc:
        print(
            "scanner startup refused at private_root_validated: " + type(exc).__name__,
            file=sys.stderr,
        )
        return 2
    asyncio.run(
        run(
            limit=args.limit,
            dry=args.dry_run,
            use_buffer=args.buffer,
            max_pass_seconds=args.max_pass_seconds,
            resolve_max_seconds=args.resolve_max_seconds,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
