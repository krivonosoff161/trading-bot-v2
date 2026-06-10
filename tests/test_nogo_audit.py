# -*- coding: utf-8 -*-
"""test_nogo_audit.py — классификация audit_bucket, join, форма выходных файлов. Без сети."""
import csv
import datetime as dt
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import nogo_audit as A  # noqa: E402

NOW = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.timezone.utc)


def _j(cid, hours_ago=100, horizon=24, **kw):
    ts = (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"card_id": cid, "ts_utc": ts, "verdict": "NO_GO", "horizon_hours": horizon,
           "asset": "BTC", "okx_inst": "BTC-USDT-SWAP", "source": "decrypt", "layer": 1,
           "headline": "h", "source_url": f"https://x.com/{cid}", "low_confidence": True,
           "chief_called": True, "in_price": "yes", "surprise": "none",
           "event_phase": "realized", "lead_class": "LAGGING", "materiality_score": 0.7,
           "price_at_decision": 100.0}
    row.update(kw)
    return row


def _o(cid, ret=0.5, mfe=1.0, mae=-1.0, excess=0.2, scored=True):
    return {"card_id": cid, "scored": scored, "verdict": "NO_GO", "ret_pct": ret,
            "mfe_long_pct": mfe, "mae_long_pct": mae, "excess_pct": excess,
            "outcome_long_pct": ret, "outcome_short_pct": (-ret if ret is not None else None),
            "missed_move": False, "verdict_correct": True}


def test_bucket_classification_all_branches():
    j = _j("a")
    assert A.classify_bucket(j, _o("a", excess=5.0), NOW) == "MISSED_IDIO_MOVE"
    assert A.classify_bucket(j, _o("a", ret=4.0, excess=1.0), NOW) == "MISSED_DIRECTIONAL_MOVE"
    assert A.classify_bucket(j, _o("a", ret=0.5, mfe=6.0, excess=1.0), NOW) == "VOLATILE_BUT_NO_DIRECTION"
    assert A.classify_bucket(j, _o("a"), NOW) == "CORRECT_NO_GO"
    assert A.classify_bucket(j, {"card_id": "a", "scored": False}, NOW) == "MANUAL_OR_UNSCORED"
    assert A.classify_bucket(_j("b", hours_ago=1, horizon=48), None, NOW) == "NOT_MATURE"
    assert A.classify_bucket(_j("c", okx_inst=None), None, NOW) == "MISSING_PRICE"
    assert A.classify_bucket(_j("d"), None, NOW) == "MANUAL_OR_UNSCORED"   # зрелая, не посчитана
    assert A.classify_bucket(_j("e"), _o("e", ret=None, mfe=None, mae=None, excess=None), NOW) == "MISSING_PRICE"


def test_build_dataset_joins_and_filters_no_go():
    journal = [_j("a"), _j("w", verdict="WATCH"), _j("b", hours_ago=1, horizon=48)]
    outcomes = [_o("a", excess=7.0), _o("a", excess=0.1)]      # последний выигрывает
    rows = A.build_dataset(journal, outcomes, NOW)
    assert len(rows) == 2                                      # WATCH не входит
    by = {r["card_id"]: r for r in rows}
    assert by["a"]["audit_bucket"] == "CORRECT_NO_GO"          # excess=0.1 (последний)
    assert by["a"]["excess_pct"] == 0.1
    assert by["b"]["audit_bucket"] == "NOT_MATURE"
    assert by["b"]["ret_pct"] is None
    assert list(by["a"].keys()) == A.FIELDS


def test_outputs_shape(tmp_path):
    rows = A.build_dataset([_j("a")], [_o("a", excess=4.0)], NOW)
    A.write_outputs(rows, tmp_path)
    data = json.loads((tmp_path / "no_go_audit.json").read_text(encoding="utf-8"))
    assert len(data) == 1 and data[0]["audit_bucket"] == "MISSED_IDIO_MOVE"
    with open(tmp_path / "no_go_audit.csv", encoding="utf-8-sig") as f:
        rdr = csv.reader(f)
        header = next(rdr)
        assert header == A.FIELDS
        assert len(list(rdr)) == 1


def test_select_chart_rows_dedup_and_caps():
    rows = []
    for i in range(30):
        rows.append({**{k: None for k in A.FIELDS}, "card_id": f"i{i}", "asset": "X",
                     "audit_bucket": "MISSED_IDIO_MOVE", "excess_pct": i,
                     "ret_pct": i, "mfe_long_pct": i, "mae_long_pct": -i})
    sel = A.select_chart_rows(rows, per_set=20)
    assert len(sel) == 20
    assert max(abs(r["excess_pct"]) for r in sel) == 29        # топ по |excess|


def test_summary_mentions_key_sections():
    journal = [_j("a"), _j("w", verdict="WATCH")]
    rows = A.build_dataset(journal, [_o("a", excess=4.0)], NOW)
    text = A.build_summary(rows, journal, 3.0)
    assert "идио" in text.lower()
    assert "in_price=yes" in text
    assert "MISSED_IDIO_MOVE" in text
