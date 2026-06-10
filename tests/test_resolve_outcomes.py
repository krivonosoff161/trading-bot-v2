# -*- coding: utf-8 -*-
"""
test_resolve_outcomes.py — отбор зрелых карточек и --limit без сети.

OKX-вызовы и пути файлов подменены; скоринг/семантика resolve не меняются.
"""
import datetime as dt
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import resolve_outcomes as RO  # noqa: E402


def _row(cid, hours_ago=100, horizon=24):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"card_id": cid, "ts_utc": ts, "horizon_hours": horizon, "verdict": "NO_GO",
            "side": "none", "asset": "BTC", "okx_inst": "BTC-USDT-SWAP", "layer": 1,
            "price_at_decision": 100.0, "btc_at_decision": 100.0,
            "baseline_symbol": "BTC-USDT-SWAP"}


def test_candidates_split_mature_vs_pending():
    now = dt.datetime.now(dt.timezone.utc)
    rows = [_row("m1"), _row("m2"), _row("p1", hours_ago=1, horizon=48), {"card_id": None}]
    matured, pending = RO._candidates(rows, set(), now)
    assert [r["card_id"] for r in matured] == ["m1", "m2"]
    assert pending == 1

    matured2, _ = RO._candidates(rows, {"m1"}, now)
    assert [r["card_id"] for r in matured2] == ["m2"]


def _patch_paths_and_net(tmp_path, monkeypatch):
    journal = tmp_path / "journal.jsonl"
    outcomes = tmp_path / "outcomes.jsonl"
    monkeypatch.setattr(RO, "JOURNAL", journal)
    monkeypatch.setattr(RO, "OUTCOMES", outcomes)
    monkeypatch.setattr(RO, "OUT_DIR", tmp_path)
    monkeypatch.setattr(RO.R, "EVENTS", tmp_path / "events.jsonl")
    monkeypatch.setattr(RO.R, "REASONING", tmp_path / "reasoning.jsonl")
    monkeypatch.setattr(RO.R, "build_training_record", lambda *a, **k: None)
    monkeypatch.setattr(RO.R, "build_memory_record", lambda *a, **k: None)

    def fake_fetch(inst, t0_ms, t_end_ms, bar="1H"):
        return [(t0_ms, 101.0, 99.0, 100.5), (t_end_ms, 102.0, 100.0, 101.0)]
    monkeypatch.setattr(RO, "fetch_path", fake_fetch)
    monkeypatch.setattr(RO, "okx_last", lambda inst: 100.0)
    return journal, outcomes


def _written(outcomes: Path) -> list[dict]:
    if not outcomes.exists():
        return []
    return [json.loads(line) for line in outcomes.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_resolve_limit_processes_only_n(tmp_path, monkeypatch):
    journal, outcomes = _patch_paths_and_net(tmp_path, monkeypatch)
    journal.write_text("\n".join(json.dumps(_row(f"c{i}")) for i in range(5)) + "\n", encoding="utf-8")

    RO.resolve(limit=2)
    rows = _written(outcomes)
    assert len(rows) == 2
    assert all(r["scored"] for r in rows)

    RO.resolve(limit=2)                       # повторный прогон продолжает очередь
    assert len(_written(outcomes)) == 4

    RO.resolve()                              # без лимита — добивает хвост
    rows = _written(outcomes)
    assert len(rows) == 5
    assert len({r["card_id"] for r in rows}) == 5


def test_resolve_no_limit_default_processes_all(tmp_path, monkeypatch):
    journal, outcomes = _patch_paths_and_net(tmp_path, monkeypatch)
    journal.write_text("\n".join(json.dumps(_row(f"c{i}")) for i in range(3)) + "\n", encoding="utf-8")
    RO.resolve()
    assert len(_written(outcomes)) == 3
