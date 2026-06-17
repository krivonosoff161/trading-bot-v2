# -*- coding: utf-8 -*-
"""
news_buffer.py - durable intake buffer for the info-edge scanner.

The buffer separates internet ingestion from LLM analysis:
source item -> extracted machine document -> normalized event -> scanner consumer.

It is intentionally small and SQLite-based so every news item can be inspected,
replayed, retried, or dropped with a reason.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import socket
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import google_news_url as GN  # noqa: E402
from src.scout import page_extract  # noqa: E402
from src.scout.asset_identity import identify_asset  # noqa: E402
from src.scout.dedup import event_key as make_event_key  # noqa: E402
from src.scout.sources import sec_edgar as SEC  # noqa: E402
from src.scout.router import baseline_for_layer, route_asset, route_temporal, score_materiality, source_meta  # noqa: E402
from src.scout.trigger_policy import get_policy  # noqa: E402

DB_PATH = _ROOT / "data" / "scout" / "news_buffer.sqlite"

STATUS_NEW = "NEW"
STATUS_EXTRACTED = "EXTRACTED"
STATUS_READY = "READY_FOR_AGENT"
STATUS_ANALYZED = "ANALYZED"
STATUS_DROPPED = "DROPPED"
STATUS_FAILED_RETRY = "FAILED_RETRY"
STATUS_FAILED_FINAL = "FAILED_FINAL"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_url(url: str) -> str:
    try:
        p = urlsplit((url or "").strip())
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", "")) or url
    except Exception:
        return url


def doc_id_for(url: str, title: str = "") -> str:
    base = canonical_url(url) or title or now_iso()
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path = DB_PATH) -> None:
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_items (
                doc_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_class TEXT,
                lead_class TEXT,
                url TEXT,
                canonical_url TEXT UNIQUE,
                title TEXT NOT NULL,
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                raw_json TEXT,
                status TEXT NOT NULL,
                drop_reason TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS machine_docs (
                doc_id TEXT PRIMARY KEY REFERENCES raw_items(doc_id) ON DELETE CASCADE,
                title TEXT,
                text TEXT,
                published_at TEXT,
                extraction_method TEXT,
                extraction_status TEXT,
                extraction_quality REAL,
                text_len INTEGER,
                content_hash TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS normalized_events (
                doc_id TEXT PRIMARY KEY REFERENCES raw_items(doc_id) ON DELETE CASCADE,
                asset TEXT,
                okx_inst TEXT,
                layer INTEGER,
                baseline_symbol TEXT,
                asset_confidence REAL,
                event_type_hint TEXT,
                phase_hint TEXT,
                materiality_score REAL,
                lead_class TEXT,
                source_id TEXT,
                source_class TEXT,
                event_key TEXT,
                machine_json TEXT,
                status TEXT NOT NULL,
                drop_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_health (
                source_id TEXT PRIMARY KEY,
                last_seen_at TEXT,
                last_ok_at TEXT,
                last_error TEXT,
                total_seen INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_items(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_norm_status ON normalized_events(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_norm_event_key ON normalized_events(event_key);
            """
        )


def _json(data: dict) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def ingest_items(items: list[dict], path: Path = DB_PATH) -> dict:
    """Upsert source items into raw_items. Returns counters."""
    init_db(path)
    inserted = updated = 0
    ts = now_iso()
    with connect(path) as conn:
        for item in items:
            title = " ".join(str(item.get("title") or "").split()).strip()
            url = str(item.get("url") or "").strip()
            if not title and not url:
                continue
            # Google-обёртка: локальный декод (старый формат) → реальный URL для canon/дедупа.
            # Новый формат локально не раскрывается — резолвится сетью на extract-стадии.
            stored_url, gn_meta = GN.unwrap_google_news_url(url)
            canon = canonical_url(stored_url)
            if item.get("event_key") and item.get("source_class") != "rss":
                canon = f"{canon}::{item['event_key']}"
            doc_id = item.get("doc_id") or doc_id_for(canon, title)
            if gn_meta.get("decoded") and not item.get("doc_id"):
                # стабильность doc_id: если эта статья уже лежит под wrapper-id — переиспользуем
                legacy_id = doc_id_for(canonical_url(url), title)
                if legacy_id != doc_id and conn.execute(
                        "SELECT 1 FROM raw_items WHERE doc_id=?", (legacy_id,)).fetchone():
                    doc_id = legacy_id
            source = item.get("source") or item.get("source_id") or "unknown"
            existing = conn.execute("SELECT status FROM raw_items WHERE doc_id=?", (doc_id,)).fetchone()
            if existing:
                updated += 1
                conn.execute(
                    """
                    UPDATE raw_items
                    SET title=COALESCE(NULLIF(?, ''), title),
                        raw_json=?,
                        fetched_at=?,
                        updated_at=?
                    WHERE doc_id=?
                    """,
                    (title, _json(item), ts, ts, doc_id),
                )
            else:
                inserted += 1
                conn.execute(
                    """
                    INSERT INTO raw_items (
                        doc_id, source_id, source_class, lead_class, url, canonical_url, title,
                        published_at, fetched_at, raw_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc_id,
                        source,
                        item.get("source_class"),
                        item.get("lead_class"),
                        stored_url,            # реальный URL (если локально декодирован); оригинал — в raw_json
                        canon,
                        title,
                        item.get("time") or item.get("published_at"),
                        ts,
                        _json(item),
                        STATUS_NEW,
                        ts,
                        ts,
                    ),
                )
            conn.execute(
                """
                INSERT INTO source_health(source_id, last_seen_at, total_seen)
                VALUES (?, ?, 1)
                ON CONFLICT(source_id) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    total_seen=total_seen + 1
                """,
                (source, ts),
            )
    return {"inserted": inserted, "updated": updated}


def _is_safe_fetch_url(url: str) -> tuple[bool, str | None]:
    """Basic SSRF guard for article fetches."""
    try:
        p = urlsplit(url or "")
    except Exception:
        return False, "bad_url"
    if p.scheme not in ("http", "https"):
        return False, "bad_scheme"
    host = (p.hostname or "").lower()
    if not host or host in ("localhost",) or host.endswith(".local"):
        return False, "blocked_host"
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, "blocked_ip"
    except ValueError:
        # Hostname: resolve a small sample and block private results.
        try:
            for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)[:3]:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                    return False, "blocked_resolved_ip"
        except Exception:
            return False, "dns_failed"
    return True, None


def _fallback_newspaper(url: str) -> dict | None:
    try:
        from newspaper import Article  # newspaper4k/newspaper3k compatible import
    except Exception:
        return None
    try:
        article = Article(url)
        article.download()
        article.parse()
        text = (article.text or "").strip()
        if not text:
            return None
        return {
            "url": url,
            "title": article.title,
            "date": article.publish_date.isoformat() if article.publish_date else None,
            "text": text,
            "method": "newspaper4k",
        }
    except Exception:
        return None


def resolve_pending(limit: int = 50, path: Path = DB_PATH, dry: bool = False) -> dict:
    """Turn raw_items into machine_docs. Does not call LLM."""
    init_db(path)
    GN.reset_pass_counter()     # сброс cap перед каждым resolve-проходом
    resolved = failed = skipped = 0
    gn_resolved = gn_failed = 0
    ts = now_iso()
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM raw_items
            WHERE status IN (?, ?)
            ORDER BY fetched_at ASC
            LIMIT ?
            """,
            (STATUS_NEW, STATUS_FAILED_RETRY, limit),
        ).fetchall()
        for row in rows:
            raw = json.loads(row["raw_json"] or "{}")
            url = row["url"] or ""
            title = row["title"] or ""
            rss_text = (raw.get("text") or raw.get("summary") or "").strip()
            method = "rss_text" if rss_text else "title_only"
            status = "partial"
            text = rss_text
            ext_title = title
            ext_date = row["published_at"]
            error = None
            fetch_url = url
            gn_original = None     # обёртка Google, если реальный URL раскрыт
            sec_meta = None        # структурные метаданные филинга SEC

            # SEC-филинги pre-routed (asset задан) → общая ветка их пропускала, доки
            # оставались title_only по 21-43 символа (аудит 11.06). Отдельная экстракция:
            # index-страница → primary document → текст + CIK/форма/accession/exhibits.
            is_sec_filing = (row["source_id"] == "sec_edgar"
                             or "sec.gov/archives/" in (url or "").lower())
            if not dry and url and is_sec_filing and len(text) < 500:
                filing = SEC.extract_filing(url)
                if filing and filing.get("text"):
                    text = filing["text"].strip()
                    ext_title = filing.get("title") or ext_title
                    method = "sec_primary_doc"
                    status = "ok" if len(text) >= 500 else "partial"
                    sec_meta = filing.get("metadata")
                else:
                    error = "sec_extract_failed"   # честный фолбэк = title_only
            elif not dry and url and len(text) < 500 and not raw.get("asset"):
                if GN.is_google_news_url(url):
                    real = GN.decode_google_news_url(url) or GN.resolve_google_news_url(url)
                    if real:
                        fetch_url, gn_original = real, url
                        gn_resolved += 1
                    else:
                        gn_failed += 1
                ok, reason = _is_safe_fetch_url(fetch_url)
                if ok:
                    ext = page_extract.extract(fetch_url)
                    if ext and ext.get("text"):
                        text = ext["text"].strip()
                        ext_title = ext.get("title") or ext_title
                        ext_date = ext.get("date") or ext_date
                        method = "trafilatura"
                        status = "ok" if len(text) >= 500 else "partial"
                    else:
                        fb = _fallback_newspaper(fetch_url)
                        if fb:
                            text = fb["text"].strip()
                            ext_title = fb.get("title") or ext_title
                            ext_date = fb.get("date") or ext_date
                            method = fb.get("method") or "newspaper4k"
                            status = "ok" if len(text) >= 500 else "partial"
                        else:
                            error = (ext or {}).get("error") if isinstance(ext, dict) else "extract_failed"
                else:
                    error = reason

            quality = 0.9 if len(text) >= 1200 else 0.6 if len(text) >= 300 else 0.25
            if not text:
                text = title
                quality = 0.15

            conn.execute(
                """
                INSERT INTO machine_docs (
                    doc_id, title, text, published_at, extraction_method, extraction_status,
                    extraction_quality, text_len, content_hash, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    title=excluded.title,
                    text=excluded.text,
                    published_at=excluded.published_at,
                    extraction_method=excluded.extraction_method,
                    extraction_status=excluded.extraction_status,
                    extraction_quality=excluded.extraction_quality,
                    text_len=excluded.text_len,
                    content_hash=excluded.content_hash,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    row["doc_id"],
                    ext_title,
                    text,
                    ext_date,
                    method,
                    status,
                    quality,
                    len(text),
                    _content_hash(text),
                    _json({"error": error, "source_url": fetch_url, "google_news_url": gn_original,
                           **({"sec_filing": sec_meta} if sec_meta else {})}),
                    ts,
                    ts,
                ),
            )
            if gn_original:
                # реальный URL — в raw_items (дедуп будущих заходов); canonical только без конфликта
                new_canon = canonical_url(fetch_url)
                conflict = conn.execute(
                    "SELECT doc_id FROM raw_items WHERE canonical_url=? AND doc_id<>?",
                    (new_canon, row["doc_id"]),
                ).fetchone()
                if conflict is None:
                    conn.execute("UPDATE raw_items SET url=?, canonical_url=?, updated_at=? WHERE doc_id=?",
                                 (fetch_url, new_canon, ts, row["doc_id"]))
                else:
                    conn.execute("UPDATE raw_items SET url=?, updated_at=? WHERE doc_id=?",
                                 (fetch_url, ts, row["doc_id"]))
            conn.execute(
                "UPDATE raw_items SET status=?, error=?, updated_at=? WHERE doc_id=?",
                (STATUS_EXTRACTED, error, ts, row["doc_id"]),
            )
            if error:
                failed += 1
            else:
                resolved += 1
        skipped = max(0, limit - len(rows))
    return {"resolved": resolved, "failed_partial": failed, "skipped_capacity": skipped,
            "gn_resolved": gn_resolved, "gn_failed": gn_failed,
            "gn_pass_cap": GN.metrics().get("google_skipped_pass_cap", 0),
            "gn_metrics": GN.metrics()}   # seen/resolved/failed/429/cooldown/backoff_seconds/pass_cap


def normalize_pending(limit: int = 100, path: Path = DB_PATH) -> dict:
    """Route extracted docs into normalized_events. Does not call LLM."""
    init_db(path)
    ready = dropped = 0
    ts = now_iso()
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT r.*, d.text, d.title AS doc_title, d.published_at AS doc_published_at,
                   d.extraction_method, d.extraction_status, d.extraction_quality, d.text_len
            FROM raw_items r
            JOIN machine_docs d ON d.doc_id = r.doc_id
            WHERE r.status=?
            ORDER BY r.updated_at ASC
            LIMIT ?
            """,
            (STATUS_EXTRACTED, limit),
        ).fetchall()
        for row in rows:
            raw = json.loads(row["raw_json"] or "{}")
            title = row["doc_title"] or row["title"]
            source = row["source_id"]
            source_class = row["source_class"] or raw.get("source_class") or "rss"
            lead_class = row["lead_class"] or raw.get("lead_class") or "LAGGING"

            drop_reason = None
            cross_layer = False
            if raw.get("asset"):
                asset = raw.get("asset")
                okx_inst = raw.get("okx_inst")
                layer = int(raw.get("layer") or 2)
                baseline = raw.get("baseline") or baseline_for_layer(layer)
                conf = 1.0
                mat = {"family": raw.get("event_type", "listing"), "score": 0.6, "drop_reason": None}
                phase = raw.get("phase") or "REALIZED"
            else:
                allowed = set(source_meta(source).get("layers") or []) or None
                routed = route_asset(title, allowed_layers=allowed)   # слои источника ограничивают кандидатов
                if not routed:
                    drop_reason = "no_tracked_asset"
                    asset = okx_inst = baseline = None
                    layer = None
                    conf = None
                    mat = {"family": None, "score": 0.0}
                    phase = "UNKNOWN"
                else:
                    asset = routed["asset"]
                    okx_inst = routed.get("okx_inst")
                    layer = int(routed["layer"])
                    baseline = routed.get("baseline")
                    conf = routed.get("confidence")
                    cross_layer = bool(routed.get("cross_layer"))   # сильный алиас вне слоя источника
                    mat = score_materiality(title, layer)
                    phase = route_temporal(title)["phase"]
                    if mat.get("drop_reason") == "noise_genre":
                        drop_reason = "noise_genre"
                    elif phase == "CONTEXT":
                        drop_reason = "context_commentary"

            event_key = raw.get("event_key") or (make_event_key(asset, title) if asset else None)

            # Identity resolution: extract from raw_json (pre-routed items) or resolve
            identity = {}
            if asset:
                if raw.get("asset_class"):
                    identity = {
                        "asset_class": raw.get("asset_class"),
                        "trigger_role": raw.get("trigger_role"),
                        "requires_context": raw.get("requires_context"),
                        "identity_reason": raw.get("identity_reason"),
                        "identity_confidence": raw.get("identity_confidence"),
                        "channel_kind": raw.get("channel_kind"),
                        "context_requirements": raw.get("context_requirements"),
                    }
                else:
                    src_meta = source_meta(source)
                    policy = get_policy(source, src_meta)
                    ident = identify_asset(asset, title or "", src_meta)
                    identity = {
                        "asset_class": ident.get("asset_class"),
                        "trigger_role": ident.get("trigger_role"),
                        "requires_context": ident.get("requires_context"),
                        "identity_reason": ident.get("reason"),
                        "identity_confidence": ident.get("confidence"),
                        "channel_kind": policy.get("channel_kind"),
                        "context_requirements": ident.get("context_requirements"),
                    }

            machine = {
                "doc_id": row["doc_id"],
                "source_id": source,
                "source_class": source_class,
                "url": row["url"],
                "canonical_url": row["canonical_url"],
                "published_at": row["doc_published_at"] or row["published_at"],
                "fetched_at": row["fetched_at"],
                "title": title,
                "text": row["text"],
                "entities": [asset] if asset else [],
                "asset_hint": asset,
                "layer_hint": layer,
                "cross_layer": cross_layer,
                "event_type_hint": mat.get("family") or raw.get("event_type") or "unclassified",
                "phase_hint": phase,
                "lead_class": lead_class,
                "asset_class": identity.get("asset_class"),
                "trigger_role": identity.get("trigger_role"),
                "requires_context": identity.get("requires_context"),
                "identity_reason": identity.get("identity_reason"),
                "identity_confidence": identity.get("identity_confidence"),
                "channel_kind": identity.get("channel_kind"),
                "extraction": {
                    "method": row["extraction_method"],
                    "status": row["extraction_status"],
                    "quality": row["extraction_quality"],
                    "text_len": row["text_len"],
                },
                "dedup": {"event_key": event_key},
            }
            event_status = STATUS_DROPPED if drop_reason else STATUS_READY
            conn.execute(
                """
                INSERT INTO normalized_events (
                    doc_id, asset, okx_inst, layer, baseline_symbol, asset_confidence,
                    event_type_hint, phase_hint, materiality_score, lead_class, source_id,
                    source_class, event_key, machine_json, status, drop_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    asset=excluded.asset,
                    okx_inst=excluded.okx_inst,
                    layer=excluded.layer,
                    baseline_symbol=excluded.baseline_symbol,
                    asset_confidence=excluded.asset_confidence,
                    event_type_hint=excluded.event_type_hint,
                    phase_hint=excluded.phase_hint,
                    materiality_score=excluded.materiality_score,
                    lead_class=excluded.lead_class,
                    source_id=excluded.source_id,
                    source_class=excluded.source_class,
                    event_key=excluded.event_key,
                    machine_json=excluded.machine_json,
                    status=excluded.status,
                    drop_reason=excluded.drop_reason,
                    updated_at=excluded.updated_at
                """,
                (
                    row["doc_id"],
                    asset,
                    okx_inst,
                    layer,
                    baseline,
                    conf,
                    mat.get("family") or raw.get("event_type") or "unclassified",
                    phase,
                    mat.get("score"),
                    lead_class,
                    source,
                    source_class,
                    event_key,
                    _json(machine),
                    event_status,
                    drop_reason,
                    ts,
                    ts,
                ),
            )
            conn.execute(
                "UPDATE raw_items SET status=?, drop_reason=?, updated_at=? WHERE doc_id=?",
                (event_status, drop_reason, ts, row["doc_id"]),
            )
            if drop_reason:
                dropped += 1
            else:
                ready += 1
    return {"ready": ready, "dropped": dropped}


def ready_items(limit: int = 50, path: Path = DB_PATH) -> list[dict]:
    init_db(path)
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT r.*, n.*, d.text, d.title AS doc_title, d.published_at AS doc_published_at,
                   d.extraction_quality, d.extraction_status, d.extraction_method
            FROM normalized_events n
            JOIN raw_items r ON r.doc_id = n.doc_id
            JOIN machine_docs d ON d.doc_id = n.doc_id
            WHERE n.status=?
            ORDER BY r.fetched_at ASC
            LIMIT ?
            """,
            (STATUS_READY, limit),
        ).fetchall()
    out = []
    for row in rows:
        try:
            mj = json.loads(row["machine_json"] or "{}")
        except Exception:
            mj = {}
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except Exception:
            raw = {}
        out.append(
            {
                "buffer_doc_id": row["doc_id"],
                "title": row["doc_title"] or row["title"],
                "url": row["url"],
                "time": row["doc_published_at"] or row["published_at"],
                "source": row["source_id"],
                "source_class": row["source_class"],
                "lead_class": row["lead_class"],
                "asset": row["asset"],
                "okx_inst": row["okx_inst"],
                "layer": row["layer"],
                "baseline": row["baseline_symbol"],
                "asset_confidence": row["asset_confidence"],
                "cross_layer": bool(mj.get("cross_layer")),
                "event_type": row["event_type_hint"],
                "phase": row["phase_hint"],
                "materiality_score": row["materiality_score"],
                "event_key": row["event_key"],
                "text": row["text"],
                "extraction_quality": row["extraction_quality"],
                "extraction_status": row["extraction_status"],
                "extraction_method": row["extraction_method"],
                "asset_class": mj.get("asset_class"),
                "trigger_role": mj.get("trigger_role"),
                "requires_context": mj.get("requires_context"),
                "identity_reason": mj.get("identity_reason"),
                "identity_confidence": mj.get("identity_confidence"),
                "channel_kind": mj.get("channel_kind"),
                "market_tape_trigger": raw.get("market_tape_trigger"),
                "move_1h_pct": raw.get("move_1h_pct"),
                "move_24h_pct": raw.get("move_24h_pct"),
                "volume_quote_24h": raw.get("volume_quote_24h"),
                "ann_type": raw.get("ann_type"),
            }
        )
    return out


def mark_status(doc_id: str, status: str, drop_reason: str | None = None, path: Path = DB_PATH) -> None:
    init_db(path)
    ts = now_iso()
    with connect(path) as conn:
        conn.execute(
            "UPDATE raw_items SET status=?, drop_reason=COALESCE(?, drop_reason), updated_at=? WHERE doc_id=?",
            (status, drop_reason, ts, doc_id),
        )
        conn.execute(
            "UPDATE normalized_events SET status=?, drop_reason=COALESCE(?, drop_reason), updated_at=? WHERE doc_id=?",
            (status, drop_reason, ts, doc_id),
        )


def decode_google_backfill(path: Path = DB_PATH, apply: bool = False,
                           reset_low_quality: bool = False) -> dict:
    """Бэкфил google-обёрток: локальный декод URL + опц. сброс низкокачественных доков
    на повторную экстракцию (реальный URL раскроет resolve_pending сетью). Без LLM/сети.

    Дефолт = dry-run (только счёт). apply пишет; перед записью — бэкап .sqlite."""
    init_db(path)
    c = {"total_google": 0, "decoded": 0, "needs_network": 0, "unchanged": 0,
         "conflicts": 0, "reset_for_reextract": 0, "skipped_analyzed": 0,
         "skipped_not_google": 0, "applied": bool(apply)}
    samples: list[str] = []
    ts = now_iso()
    if apply:
        import shutil
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_suffix(f".sqlite.bak-{stamp}"))
    with connect(path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0]
        rows = conn.execute(
            """
            SELECT r.doc_id, r.url, r.canonical_url, r.status,
                   d.text_len, d.extraction_quality
            FROM raw_items r LEFT JOIN machine_docs d ON d.doc_id = r.doc_id
            WHERE r.url LIKE '%news.google.com%'
            """
        ).fetchall()
        google_rows = [r for r in rows if GN.is_google_news_url(r["url"] or "")]
        c["total_google"] = len(google_rows)
        c["skipped_not_google"] = total - len(google_rows)
        for row in google_rows:
            real = GN.decode_google_news_url(row["url"])
            if real:
                new_canon = canonical_url(real)
                conflict = conn.execute(
                    "SELECT doc_id FROM raw_items WHERE canonical_url=? AND doc_id<>?",
                    (new_canon, row["doc_id"]),
                ).fetchone()
                if conflict:
                    c["conflicts"] += 1
                else:
                    c["decoded"] += 1
                    if apply:
                        conn.execute(
                            "UPDATE raw_items SET url=?, canonical_url=?, updated_at=? WHERE doc_id=?",
                            (real, new_canon, ts, row["doc_id"]),
                        )
            else:
                c["needs_network"] += 1

            low_quality = (row["text_len"] is None or int(row["text_len"] or 0) < 500
                           or float(row["extraction_quality"] or 0.0) < 0.5)
            if not low_quality:
                c["unchanged"] += 1
                continue
            if row["status"] == STATUS_ANALYZED:
                c["skipped_analyzed"] += 1            # уже в журнале — не пере-кардить
                continue
            if reset_low_quality:
                c["reset_for_reextract"] += 1
                if len(samples) < 5:
                    samples.append(f"{row['doc_id']} {str(row['url'])[:70]}")
                if apply:
                    conn.execute(
                        "UPDATE raw_items SET status=?, drop_reason=NULL, error=NULL, updated_at=? WHERE doc_id=?",
                        (STATUS_NEW, ts, row["doc_id"]),
                    )
    c["sample_reset"] = samples
    return c


def sec_reextract(limit: int = 20, path: Path = DB_PATH, apply: bool = False,
                  fetch=None) -> dict:
    """Пере-экстракция SEC-доков, застрявших title_only (аудит 11.06: 9/9 по 21-43 симв).

    Дефолт = dry-run (только счёт + сэмплы); --apply пишет machine_docs. Хорошие доки
    (sec_primary_doc с телом) не трогаем. Статусы raw_items НЕ меняются — уже
    ANALYZED-карточки не пере-кардятся, выигрывает датасет и будущие решения."""
    init_db(path)
    c = {"checked": 0, "candidates": 0, "extracted": 0, "failed": 0,
         "skipped_good": 0, "applied": bool(apply)}
    samples: list[dict] = []
    ts = now_iso()
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT r.doc_id, r.url, m.extraction_method, m.text_len, m.metadata_json
            FROM raw_items r JOIN machine_docs m ON m.doc_id = r.doc_id
            WHERE r.source_id='sec_edgar'
            ORDER BY r.fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            c["checked"] += 1
            if row["extraction_method"] == "sec_primary_doc" and int(row["text_len"] or 0) >= 300:
                c["skipped_good"] += 1
                continue
            c["candidates"] += 1
            filing = SEC.extract_filing(row["url"], fetch=fetch)
            if not filing or not filing.get("text"):
                c["failed"] += 1
                continue
            text = filing["text"].strip()
            c["extracted"] += 1
            if len(samples) < 10:
                samples.append({"doc_id": row["doc_id"], "title": filing.get("title"),
                                "before_len": int(row["text_len"] or 0), "after_len": len(text)})
            if apply:
                quality = 0.9 if len(text) >= 1200 else 0.6 if len(text) >= 300 else 0.25
                try:
                    meta = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                meta["sec_filing"] = filing.get("metadata")
                conn.execute(
                    """
                    UPDATE machine_docs
                    SET title=?, text=?, extraction_method='sec_primary_doc',
                        extraction_status=?, extraction_quality=?, text_len=?,
                        content_hash=?, metadata_json=?, updated_at=?
                    WHERE doc_id=?
                    """,
                    (filing.get("title"), text, "ok" if len(text) >= 500 else "partial",
                     quality, len(text), _content_hash(text), _json(meta), ts, row["doc_id"]),
                )
    c["samples"] = samples
    return c


def stats(path: Path = DB_PATH) -> dict:
    init_db(path)
    with connect(path) as conn:
        raw = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM raw_items GROUP BY status")}
        norm = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM normalized_events GROUP BY status")}
        by_source = {r["source_id"]: r["n"] for r in conn.execute("SELECT source_id, COUNT(*) n FROM raw_items GROUP BY source_id")}
    return {"db": str(path), "raw": raw, "normalized": norm, "by_source": by_source}


def show(doc_id: str, path: Path = DB_PATH) -> dict | None:
    init_db(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT r.*, d.text, d.extraction_method, d.extraction_status, d.extraction_quality,
                   n.machine_json, n.asset, n.layer, n.event_key, n.status AS event_status, n.drop_reason AS event_drop
            FROM raw_items r
            LEFT JOIN machine_docs d ON d.doc_id = r.doc_id
            LEFT JOIN normalized_events n ON n.doc_id = r.doc_id
            WHERE r.doc_id=?
            """,
            (doc_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("machine_json"):
        d["machine_json"] = json.loads(d["machine_json"])
    if d.get("text"):
        d["text_preview"] = d["text"][:1000]
        d.pop("text", None)
    return d


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p_stats = sub.add_parser("stats")
    p_stats.add_argument("--db", default=str(DB_PATH))
    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("--limit", type=int, default=50)
    p_resolve.add_argument("--dry", action="store_true")
    p_norm = sub.add_parser("normalize")
    p_norm.add_argument("--limit", type=int, default=100)
    p_ready = sub.add_parser("ready")
    p_ready.add_argument("--limit", type=int, default=20)
    p_show = sub.add_parser("show")
    p_show.add_argument("doc_id")
    p_sec = sub.add_parser("sec-reextract", help="пере-экстракция SEC title_only доков (дефолт dry-run)")
    p_sec.add_argument("--limit", type=int, default=20)
    p_sec.add_argument("--apply", action="store_true", help="записать (иначе dry-run)")
    p_sec.add_argument("--db", default=str(DB_PATH))
    p_dg = sub.add_parser("decode-google", help="бэкфил google-обёрток (дефолт dry-run)")
    p_dg.add_argument("--dry-run", action="store_true", help="явный dry-run (это поведение по умолчанию)")
    p_dg.add_argument("--apply", action="store_true", help="применить (иначе dry-run)")
    p_dg.add_argument("--reset-low-quality", action="store_true",
                      help="сбросить заголовок-only google-доки на повторную экстракцию")
    p_dg.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    if args.cmd == "init":
        init_db()
        print(f"ok: {DB_PATH}")
    elif args.cmd == "stats":
        _print_json(stats(Path(args.db)))
    elif args.cmd == "resolve":
        _print_json(resolve_pending(args.limit, dry=args.dry))
    elif args.cmd == "normalize":
        _print_json(normalize_pending(args.limit))
    elif args.cmd == "ready":
        _print_json(ready_items(args.limit))
    elif args.cmd == "show":
        _print_json(show(args.doc_id) or {"error": "not_found", "doc_id": args.doc_id})
    elif args.cmd == "sec-reextract":
        _print_json(sec_reextract(args.limit, Path(args.db), apply=args.apply))
    elif args.cmd == "decode-google":
        if args.apply and args.dry_run:
            ap.error("decode-google: --dry-run and --apply are mutually exclusive")
        _print_json(decode_google_backfill(Path(args.db), apply=args.apply,
                                           reset_low_quality=args.reset_low_quality))


if __name__ == "__main__":
    main()
