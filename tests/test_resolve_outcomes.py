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


# ── семантика скоринга (аудит 11.06): beta_blind + side-aware WATCH ──────────
def test_self_baseline_sets_beta_blind_and_null_excess(tmp_path, monkeypatch):
    journal, outcomes = _patch_paths_and_net(tmp_path, monkeypatch)
    journal.write_text(json.dumps(_row("btc1")) + "\n", encoding="utf-8")   # BTC vs BTC
    RO.resolve()
    rec = _written(outcomes)[0]
    assert rec["beta_blind"] is True
    assert rec["excess_pct"] is None             # idio≡0 by construction → не пишем фиктивный 0
    assert rec["baseline_ret_pct"] == rec["ret_pct"]


def test_distinct_baseline_keeps_excess(tmp_path, monkeypatch):
    journal, outcomes = _patch_paths_and_net(tmp_path, monkeypatch)
    row = {**_row("eth1"), "asset": "ETH", "okx_inst": "ETH-USDT-SWAP",
           "baseline_symbol": "BTC-USDT-SWAP"}
    journal.write_text(json.dumps(row) + "\n", encoding="utf-8")
    RO.resolve()
    rec = _written(outcomes)[0]
    assert rec["beta_blind"] is False
    assert rec["excess_pct"] is not None


def _watch_row(cid, side, **kw):
    row = {**_row(cid), "verdict": "WATCH", "side": side,
           "asset": "ETH", "okx_inst": "ETH-USDT-SWAP", "baseline_symbol": "BTC-USDT-SWAP"}
    row.update(kw)
    return row


def test_watch_long_positive_move_is_correct(tmp_path, monkeypatch):
    journal, outcomes = _patch_paths_and_net(tmp_path, monkeypatch)   # путь даёт ret +1%
    journal.write_text(json.dumps(_watch_row("w1", "long")) + "\n", encoding="utf-8")
    RO.resolve()
    rec = _written(outcomes)[0]
    assert rec["watch_kind"] == "directional"
    assert rec["verdict_correct"] is True        # раньше WATCH всегда None → correct 0%


def test_watch_short_negative_move_is_correct(tmp_path, monkeypatch):
    journal, outcomes = _patch_paths_and_net(tmp_path, monkeypatch)

    def falling(inst, t0_ms, t_end_ms, bar="1H"):
        return [(t0_ms, 101.0, 98.0, 100.0), (t_end_ms, 100.0, 97.0, 98.0)]   # ret −2%
    monkeypatch.setattr(RO, "fetch_path", falling)
    journal.write_text(json.dumps(_watch_row("w2", "short")) + "\n", encoding="utf-8")
    RO.resolve()
    rec = _written(outcomes)[0]
    assert rec["verdict_correct"] is True
    assert rec["watch_kind"] == "directional"


def test_watch_none_is_movement_watch_not_directional(tmp_path, monkeypatch):
    journal, outcomes = _patch_paths_and_net(tmp_path, monkeypatch)

    def big_move(inst, t0_ms, t_end_ms, bar="1H"):
        return [(t0_ms, 106.0, 99.0, 100.0), (t_end_ms, 107.0, 100.0, 105.0)]  # ход +5%
    monkeypatch.setattr(RO, "fetch_path", big_move)
    journal.write_text(json.dumps(_watch_row("w3", "none")) + "\n", encoding="utf-8")
    RO.resolve()
    rec = _written(outcomes)[0]
    assert rec["watch_kind"] == "movement"
    assert rec["verdict_correct"] is None        # не направленный успех/провал
    assert rec["movement_observed"] is True
