# -*- coding: utf-8 -*-
"""
test_google_news_throttle.py — троттлинг/backoff сетевого google-резолва (без сети).

Аудит 11.06: бэкфил ~600×2 запросов → 429 «unusual traffic» на день, 466 обёрток
не раскрыто. Контракт: пауза между резолвами, cooldown после 429 (мягкий отказ),
оригинальный URL остаётся фолбэком, исключения не пролетают наружу.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import google_news_url as GN  # noqa: E402
from src.scout import news_buffer as NB  # noqa: E402

WRAPPED = "https://news.google.com/rss/articles/AU_yqLTESTID123?oc=5"

PAGE_OK = '<html><body data-n-a-sg="SGVAL" data-n-a-ts="12345">x</body></html>'
BATCH_OK = (')]}\'\n\n123\n[["wrb.fr","Fbv4je",'
            '"[\\"garturlres\\",\\"https://example.com/real-article\\"]",null,null,null,"generic"]]')


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class _Http:
    """Мок requests: программируемые ответы + счётчик вызовов."""
    def __init__(self, page=PAGE_OK, batch=BATCH_OK, page_status=200):
        self.calls = 0
        self._page, self._batch, self._page_status = page, batch, page_status

    def get(self, url, **kw):
        self.calls += 1
        return _Resp(self._page, self._page_status)

    def post(self, url, **kw):
        self.calls += 1
        return _Resp(self._batch)


class _Clock:
    """Фейковое время: sleep двигает now, реального ожидания нет."""
    def __init__(self):
        self.t = 1000.0
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept += s
        self.t += s


def _patch_clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(GN, "_now", clock.now)
    monkeypatch.setattr(GN, "_sleep", clock.sleep)
    GN.reset_metrics()
    return clock


def test_success_path_resolves_and_counts(monkeypatch):
    _patch_clock(monkeypatch)
    http = _Http()
    real = GN.resolve_google_news_url(WRAPPED, http=http)
    assert real == "https://example.com/real-article"
    m = GN.metrics()
    assert m["google_wrappers_seen"] == 1 and m["google_resolved"] == 1
    assert m["google_429"] == 0 and m["google_failed"] == 0


def test_throttle_spaces_out_requests(monkeypatch):
    clock = _patch_clock(monkeypatch)
    GN.resolve_google_news_url(WRAPPED, http=_Http())
    GN.resolve_google_news_url(WRAPPED, http=_Http())
    # между сетевыми запросами выдержана пауза RESOLVE_DELAY_S (учтена в метрике)
    assert clock.slept > 0
    assert GN.metrics()["google_backoff_seconds"] > 0


def test_429_enters_cooldown_and_soft_fails(monkeypatch):
    _patch_clock(monkeypatch)
    http429 = _Http(page="too many requests", page_status=429)
    assert GN.resolve_google_news_url(WRAPPED, http=http429) is None   # мягкий отказ
    m = GN.metrics()
    assert m["google_429"] == 1 and GN.in_cooldown()

    # в cooldown сеть НЕ трогается вообще
    http2 = _Http()
    assert GN.resolve_google_news_url(WRAPPED, http=http2) is None
    assert http2.calls == 0
    assert GN.metrics()["google_skipped_cooldown"] == 1


def test_unusual_traffic_text_treated_as_429(monkeypatch):
    _patch_clock(monkeypatch)
    http = _Http(page="Our systems have detected unusual traffic from your network")
    assert GN.resolve_google_news_url(WRAPPED, http=http) is None
    assert GN.metrics()["google_429"] == 1


def test_cooldown_expires(monkeypatch):
    clock = _patch_clock(monkeypatch)
    GN.resolve_google_news_url(WRAPPED, http=_Http(page="x", page_status=429))
    assert GN.in_cooldown()
    clock.t += GN.COOLDOWN_429_S + 1
    assert not GN.in_cooldown()
    assert GN.resolve_google_news_url(WRAPPED, http=_Http()) == "https://example.com/real-article"


def test_no_exception_propagation(monkeypatch):
    _patch_clock(monkeypatch)

    class _Boom:
        def get(self, *a, **kw):
            raise RuntimeError("network down")
    assert GN.resolve_google_news_url(WRAPPED, http=_Boom()) is None
    assert GN.metrics()["google_failed"] == 1


def test_resolver_failure_keeps_original_url_as_fallback(monkeypatch, tmp_path):
    """resolve_pending: сбой резолва → title_only док, исходная обёртка остаётся URL-ом,
    дубликатов machine_docs нет."""
    _patch_clock(monkeypatch)
    db = tmp_path / "nb_gn.sqlite"
    item = {"title": "Gold rallies on safe haven demand", "url": WRAPPED,
            "time": "2026-06-11", "source": "google_news_metals",
            "source_class": "rss", "lead_class": "LAGGING"}
    NB.ingest_items([item], path=db)
    monkeypatch.setattr(NB.GN, "decode_google_news_url", lambda u: None)
    monkeypatch.setattr(NB.GN, "resolve_google_news_url", lambda u: None)
    monkeypatch.setattr(NB.page_extract, "extract", lambda u: None)
    monkeypatch.setattr(NB, "_fallback_newspaper", lambda u: None)

    res = NB.resolve_pending(limit=10, path=db, dry=False)
    assert res["gn_failed"] == 1 and "gn_metrics" in res

    import sqlite3
    con = sqlite3.connect(db)
    rows = con.execute("select r.url, m.extraction_method from raw_items r "
                       "join machine_docs m on m.doc_id=r.doc_id").fetchall()
    assert len(rows) == 1                              # без дублей
    assert rows[0][0] == WRAPPED                       # обёртка осталась фолбэком
    assert rows[0][1] == "title_only"
