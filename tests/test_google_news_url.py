# -*- coding: utf-8 -*-
"""
test_google_news_url.py — декодер google-обёрток + врезка в news_buffer.

Все фикстуры синтетические (никаких приватных/реальных URL); сеть подменена.
"""
import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import google_news_url as GN  # noqa: E402
from src.scout import news_buffer as NB  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_throttle(monkeypatch):
    """Троттлинг резолвера (фича 11.06) не должен реально спать в юнит-тестах."""
    GN.reset_metrics()
    monkeypatch.setattr(GN, "_sleep", lambda s: None)


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _old_format_url(real_url: str) -> str:
    """Собрать синтетическую обёртку старого формата (URL зашит в protobuf)."""
    payload = b"\x08\x13\x22" + _varint(len(real_url)) + real_url.encode() + b"\xd2\x01\x00"
    art = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"https://news.google.com/rss/articles/{art}?oc=5"


def _new_format_url() -> str:
    payload = b"\x08\x13\x22\x10" + b"AU_yqLfakefake12"
    art = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"https://news.google.com/rss/articles/{art}"


def test_is_google_news_url():
    assert GN.is_google_news_url("https://news.google.com/rss/articles/abc")
    assert GN.is_google_news_url("https://news.google.com/articles/abc?hl=en")
    assert not GN.is_google_news_url("https://decrypt.co/articles/abc")
    assert not GN.is_google_news_url("https://news.google.com/rss/search?q=x")
    assert not GN.is_google_news_url(None)


def test_decode_old_format_returns_embedded_url():
    real = "https://example.com/gold-fed-rates-article"
    assert GN.decode_google_news_url(_old_format_url(real)) == real


def test_decode_old_format_long_url_two_byte_varint():
    real = "https://example.com/" + "a" * 200      # длина >127 → двухбайтовый varint
    assert GN.decode_google_news_url(_old_format_url(real)) == real


def test_decode_new_format_returns_none():
    assert GN.decode_google_news_url(_new_format_url()) is None


def test_decode_malformed_never_throws():
    assert GN.decode_google_news_url("https://news.google.com/rss/articles/%%%не-base64%%%") is None
    assert GN.decode_google_news_url("") is None
    assert GN.decode_google_news_url("not a url at all") is None


def test_unwrap_meta_preserves_original():
    real = "https://example.com/story"
    wrapped = _old_format_url(real)
    url, meta = GN.unwrap_google_news_url(wrapped)
    assert url == real
    assert meta["google_news"] and meta["decoded"]
    assert meta["google_news_url"] == wrapped

    plain = "https://decrypt.co/story"
    url2, meta2 = GN.unwrap_google_news_url(plain)
    assert url2 == plain and not meta2["google_news"]

    nf = _new_format_url()
    url3, meta3 = GN.unwrap_google_news_url(nf)
    assert url3 == nf and meta3["google_news"] and not meta3["decoded"]


class _FakeHTTP:
    """requests-подобный объект: страница статьи + ответ batchexecute."""

    def __init__(self, page_text: str, post_text: str):
        self.page_text, self.post_text = page_text, post_text
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("get", url))
        return SimpleNamespace(text=self.page_text, status_code=200)

    def post(self, url, **kw):
        self.calls.append(("post", url))
        return SimpleNamespace(text=self.post_text, status_code=200)


def _batch_response(real_url: str) -> str:
    inner = f'["garturlres","{real_url}"]'.replace('"', '\\"')
    return ")]}'\n\n123\n" + f'[["wrb.fr","Fbv4je","{inner}",null,null,null,"generic"]]'


def test_resolver_happy_path_with_fake_http():
    http = _FakeHTTP(
        page_text='<c-wiz data-n-a-sg="SIG123" data-n-a-ts="99999">x</c-wiz>',
        post_text=_batch_response("https://example.com/real-article"),
    )
    out = GN.resolve_google_news_url(_new_format_url(), http=http)
    assert out == "https://example.com/real-article"
    assert [c[0] for c in http.calls] == ["get", "post"]


def test_resolver_returns_none_without_sg_ts():
    http = _FakeHTTP(page_text="<html>consent wall</html>", post_text="")
    assert GN.resolve_google_news_url(_new_format_url(), http=http) is None


def test_resolver_returns_none_on_garbage_post():
    http = _FakeHTTP(page_text='<x data-n-a-sg="S" data-n-a-ts="1">', post_text="not json")
    assert GN.resolve_google_news_url(_new_format_url(), http=http) is None
    assert GN.resolve_google_news_url("https://decrypt.co/x", http=http) is None


# ── врезка в news_buffer ─────────────────────────────────────────────────────
def _google_item(url: str) -> dict:
    return {"title": "Gold jumps as Fed cut looms", "url": url, "time": "2026-06-09",
            "source": "google_news_metals", "source_class": "rss", "lead_class": "LAGGING"}


def test_ingest_uses_locally_decoded_url_for_canonical(tmp_path):
    db = tmp_path / "nb.sqlite"
    real = "https://example.com/gold-story"
    NB.ingest_items([_google_item(_old_format_url(real))], path=db)
    with NB.connect(db) as conn:
        row = conn.execute("SELECT doc_id, url, canonical_url, raw_json FROM raw_items").fetchone()
    assert row["url"] == real
    assert row["canonical_url"] == NB.canonical_url(real)
    assert row["doc_id"] == NB.doc_id_for(NB.canonical_url(real), "Gold jumps as Fed cut looms")
    assert "news.google.com" in row["raw_json"]          # оригинальная обёртка сохранена


def test_ingest_non_google_unchanged(tmp_path):
    db = tmp_path / "nb.sqlite"
    NB.ingest_items([{"title": "ZEC bug", "url": "https://decrypt.co/zec-bug",
                      "source": "decrypt", "source_class": "rss"}], path=db)
    with NB.connect(db) as conn:
        row = conn.execute("SELECT url, canonical_url FROM raw_items").fetchone()
    assert row["url"] == "https://decrypt.co/zec-bug"


def test_resolve_pending_resolves_google_and_persists_real_url(tmp_path, monkeypatch):
    db = tmp_path / "nb.sqlite"
    NB.ingest_items([_google_item(_new_format_url())], path=db)
    monkeypatch.setattr(NB.GN, "resolve_google_news_url", lambda u, **k: "https://example.com/gold-story")
    monkeypatch.setattr(NB, "_is_safe_fetch_url", lambda u: (True, None))
    monkeypatch.setattr(NB.page_extract, "extract",
                        lambda u: ({"text": "Gold body paragraph. " * 60, "title": "Gold story",
                                    "date": "2026-06-09"} if u == "https://example.com/gold-story"
                                   else {"error": "wrong-url"}))
    res = NB.resolve_pending(limit=10, path=db, dry=False)
    assert res["gn_resolved"] == 1 and res["resolved"] == 1
    with NB.connect(db) as conn:
        raw = conn.execute("SELECT url, canonical_url FROM raw_items").fetchone()
        doc = conn.execute("SELECT text_len, metadata_json FROM machine_docs").fetchone()
    assert raw["url"] == "https://example.com/gold-story"
    assert raw["canonical_url"] == "https://example.com/gold-story"
    assert doc["text_len"] > 500
    assert "news.google.com" in doc["metadata_json"]     # google_news_url сохранён в метаданных


def test_resolve_pending_conflict_keeps_old_canonical(tmp_path, monkeypatch):
    db = tmp_path / "nb.sqlite"
    NB.ingest_items([{"title": "Direct story", "url": "https://example.com/gold-story",
                      "source": "oilprice", "source_class": "rss",
                      "text": "direct body " * 60}], path=db)
    NB.ingest_items([_google_item(_new_format_url())], path=db)
    monkeypatch.setattr(NB.GN, "resolve_google_news_url", lambda u, **k: "https://example.com/gold-story")
    monkeypatch.setattr(NB, "_is_safe_fetch_url", lambda u: (True, None))
    monkeypatch.setattr(NB.page_extract, "extract", lambda u: {"text": "body " * 200, "title": "t", "date": None})
    NB.resolve_pending(limit=10, path=db, dry=False)
    with NB.connect(db) as conn:
        g = conn.execute("SELECT url, canonical_url FROM raw_items WHERE url LIKE '%example.com%' AND canonical_url LIKE '%news.google.com%'").fetchone()
    assert g is not None                                  # url обновлён, canonical остался (конфликт)


def test_resolve_pending_non_google_does_not_touch_resolver(tmp_path, monkeypatch):
    db = tmp_path / "nb.sqlite"
    NB.ingest_items([{"title": "ZEC bug crisis deepens", "url": "https://decrypt.co/zec-bug",
                      "source": "decrypt", "source_class": "rss",
                      "text": "Zcash body. " * 60}], path=db)
    called = []
    monkeypatch.setattr(NB.GN, "resolve_google_news_url", lambda u, **k: called.append(u))
    res = NB.resolve_pending(limit=10, path=db, dry=False)
    assert res["gn_resolved"] == 0 and not called


def test_decode_google_backfill_dry_run_then_apply(tmp_path, monkeypatch):
    db = tmp_path / "nb.sqlite"
    NB.ingest_items([_google_item(_new_format_url())], path=db)
    monkeypatch.setattr(NB.GN, "resolve_google_news_url", lambda u, **k: None)   # тело не достали
    monkeypatch.setattr(NB, "_is_safe_fetch_url", lambda u: (True, None))
    monkeypatch.setattr(NB.page_extract, "extract", lambda u: None)
    NB.resolve_pending(limit=10, path=db, dry=False)      # → title-only док, status=EXTRACTED

    dry = NB.decode_google_backfill(path=db, apply=False, reset_low_quality=True)
    assert dry["total_google"] == 1 and dry["needs_network"] == 1
    assert dry["reset_for_reextract"] == 1 and dry["applied"] is False
    with NB.connect(db) as conn:
        assert conn.execute("SELECT status FROM raw_items").fetchone()["status"] == NB.STATUS_EXTRACTED

    applied = NB.decode_google_backfill(path=db, apply=True, reset_low_quality=True)
    assert applied["reset_for_reextract"] == 1 and applied["applied"] is True
    with NB.connect(db) as conn:
        assert conn.execute("SELECT status FROM raw_items").fetchone()["status"] == NB.STATUS_NEW
    assert list(tmp_path.glob("*.bak-*"))                 # бэкап БД создан


def test_decode_google_backfill_skips_analyzed_and_non_google(tmp_path):
    db = tmp_path / "nb.sqlite"
    NB.ingest_items([_google_item(_new_format_url()),
                     {"title": "Direct", "url": "https://decrypt.co/x", "source": "decrypt",
                      "source_class": "rss"}], path=db)
    NB.resolve_pending(limit=10, path=db, dry=True)
    with NB.connect(db) as conn:
        gid = conn.execute("SELECT doc_id FROM raw_items WHERE url LIKE '%news.google.com%'").fetchone()["doc_id"]
    NB.mark_status(gid, NB.STATUS_ANALYZED, path=db)

    rep = NB.decode_google_backfill(path=db, apply=True, reset_low_quality=True)
    assert rep["skipped_analyzed"] == 1 and rep["reset_for_reextract"] == 0
    assert rep["skipped_not_google"] == 1
    with NB.connect(db) as conn:
        assert conn.execute("SELECT status FROM raw_items WHERE doc_id=?", (gid,)).fetchone()["status"] == NB.STATUS_ANALYZED


def test_decode_google_backfill_old_format_updates_url(tmp_path):
    db = tmp_path / "nb.sqlite"
    real = "https://example.com/embedded-story"
    # обойти ingest-декод: вставить wrapper-строку напрямую (исторические данные)
    NB.init_db(db)
    wrapped = _old_format_url(real)
    with NB.connect(db) as conn:
        conn.execute(
            "INSERT INTO raw_items (doc_id, source_id, url, canonical_url, title, fetched_at, status, created_at, updated_at) "
            "VALUES ('abc123', 'google_news_metals', ?, ?, 'Old story', '2026-06-09', 'EXTRACTED', '2026-06-09', '2026-06-09')",
            (wrapped, NB.canonical_url(wrapped)),
        )
    rep = NB.decode_google_backfill(path=db, apply=True, reset_low_quality=False)
    assert rep["decoded"] == 1
    with NB.connect(db) as conn:
        row = conn.execute("SELECT url, canonical_url FROM raw_items").fetchone()
    assert row["url"] == real and row["canonical_url"] == NB.canonical_url(real)
