# -*- coding: utf-8 -*-
"""Keyless public orderbook+trades recorder: normalized polling, rotation, retention, status/readiness,
stop-file. No keys/orders/private endpoints. Research-only."""
import ast
import gzip
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import micro_recorder as MR  # noqa: E402


def _fake_get(url):
    if "/books" in url:
        return {"data": [{"bids": [["100", "3"], ["99.9", "10"]], "asks": [["100.1", "1"]], "ts": "1"}]}
    return {"data": [{"ts": "1", "side": "buy", "px": "100", "sz": "2"}]}


class TestPoll:
    def test_poll_symbol_normalizes_public_only(self):
        rec = MR.poll_symbol("BTC-USDT-SWAP", depth=50, http_get=_fake_get)
        assert rec["symbol"] == "BTC-USDT-SWAP"
        assert rec["book"]["bids"] == [["100", "3"], ["99.9", "10"]]
        assert len(rec["trades"]) == 1 and rec["trades"][0]["side"] == "buy"


class TestWriteAndStatus:
    def test_write_and_status_counts(self, tmp_path):
        rec = MR.poll_symbol("BTC-USDT-SWAP", depth=5, http_get=_fake_get)
        MR.write_record(tmp_path, rec, date_utc="d1")
        MR.write_record(tmp_path, rec, date_utc="d1")
        st = MR.status(tmp_path)
        assert st["records"] == 2 and st["symbols"] == ["BTC-USDT-SWAP"]
        assert st["readiness"] == "collecting_insufficient"  # below the gate

    def test_records_are_gzip_jsonl(self, tmp_path):
        MR.write_record(tmp_path, MR.poll_symbol("X-USDT-SWAP", depth=5, http_get=_fake_get), date_utc="d1")
        p = next((tmp_path / "microstructure" / "recordings").rglob("*.jsonl.gz"))
        row = json.loads(gzip.open(p, "rt", encoding="utf-8").readline())
        assert row["symbol"] == "X-USDT-SWAP" and "book" in row


class TestRetention:
    def test_prune_disk_removes_oldest_over_cap(self, tmp_path):
        for i in range(5):
            rec = MR.poll_symbol(f"S{i}-USDT-SWAP", depth=5, http_get=_fake_get)
            for _ in range(50):
                MR.write_record(tmp_path, rec, date_utc="d1")
        removed = MR.prune_disk(tmp_path, max_disk_mb=0.0)  # cap 0 -> prune all
        assert removed > 0


class TestStopFileBounded:
    def test_record_stops_on_stop_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(MR, "is_stop_requested", lambda _r: True, raising=False)
        # is_stop_requested is imported inside record(); patch the source module instead
        import src.research_lab.stop_intent as SI
        monkeypatch.setattr(SI, "is_stop_requested", lambda _r: True)
        out = MR.record(tmp_path, ["BTC-USDT-SWAP"], duration_s=5.0, interval_s=0.01, http_get=_fake_get)
        assert out["polls"] == 0 and out["stopped_early"] is True


class TestNoKeysNoOrders:
    def test_no_forbidden_imports_or_auth(self):
        src = (_ROOT / "src" / "research_lab" / "micro_recorder.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv", "hmac", "OK-ACCESS")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f.lower() in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        # only public endpoints, no request signing (the word "secrets" in the disclaimer is fine;
        # what must be absent is actual auth: signed headers / passphrase / hmac signing)
        assert "/market/books" in src and "/market/trades" in src
        assert "OK-ACCESS" not in src and "passphrase" not in src
        assert "secret_key" not in src and "_sign(" not in src and "hmac" not in src.lower()
