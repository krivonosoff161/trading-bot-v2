# -*- coding: utf-8 -*-
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import news_buffer as NB  # noqa: E402


def test_news_buffer_ingest_resolve_normalize_ready(tmp_path):
    db = tmp_path / "news_buffer.sqlite"
    item = {
        "title": "ZEC Crashes 38% as Zcash Discloses Critical Counterfeiting Vulnerability",
        "url": "https://decrypt.co/example-zec-bug",
        "time": "2026-06-06",
        "source": "decrypt",
        "source_class": "rss",
        "lead_class": "LAGGING",
        "text": "Zcash disclosed a critical counterfeiting vulnerability. " * 20,
    }

    assert NB.ingest_items([item], path=db)["inserted"] == 1
    assert NB.resolve_pending(limit=10, path=db, dry=True)["resolved"] == 1
    norm = NB.normalize_pending(limit=10, path=db)
    assert norm == {"ready": 1, "dropped": 0}

    ready = NB.ready_items(limit=10, path=db)
    assert len(ready) == 1
    assert ready[0]["asset"] == "ZEC"
    assert ready[0]["layer"] == 2
    assert ready[0]["event_key"] == "ZEC::security_incident"
    assert ready[0]["text"]

    NB.mark_status(ready[0]["buffer_doc_id"], NB.STATUS_ANALYZED, path=db)
    stats = NB.stats(path=db)
    assert stats["raw"][NB.STATUS_ANALYZED] == 1
