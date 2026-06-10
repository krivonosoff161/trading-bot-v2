# -*- coding: utf-8 -*-
"""
test_calibration_report.py — offline-агрегаты калибровки (missed NO_GO по разрезам).

Всё на синтетике, без файлов и сети: summarize() принимает строки явно.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout import calibration_report as C  # noqa: E402


def _jrow(cid, source, layer, **kw):
    row = {
        "card_id": cid, "verdict": "NO_GO", "source": source, "layer": layer,
        "asset": "BTC", "event_phase": "realized", "lead_class": "LAGGING",
        "chief_called": True, "low_confidence": False, "materiality_score": 0.8,
        "in_price": "yes", "surprise": "none",
    }
    row.update(kw)
    return row


def _orow(cid, mfe, mae, ret, excess):
    return {"card_id": cid, "scored": True, "verdict": "NO_GO",
            "mfe_long_pct": mfe, "mae_long_pct": mae, "ret_pct": ret, "excess_pct": excess}


def test_classify_miss_threshold():
    o = {"mfe_long_pct": 4.0, "mae_long_pct": -1.0, "ret_pct": 2.0, "excess_pct": 2.5}
    assert C.classify_miss(o, 3.0) == {"vol": True, "dir": False, "idio": False}
    assert C.classify_miss(o, 5.0) == {"vol": False, "dir": False, "idio": False}
    assert C.classify_miss({"mfe_long_pct": 1, "mae_long_pct": -8, "ret_pct": -7, "excess_pct": None}, 3.0) \
        == {"vol": True, "dir": True, "idio": None}


def test_summarize_aggregates_by_source_and_layer():
    journal = [
        _jrow("a", "decrypt", 2),
        _jrow("b", "google_news_metals", 3),
    ]
    outcomes = [
        _orow("a", mfe=10.0, mae=-2.0, ret=8.0, excess=7.5),   # vol+dir+idio промах
        _orow("b", mfe=1.0, mae=-0.5, ret=0.2, excess=0.1),    # чистый NO_GO
    ]
    rep = C.summarize(journal_rows=journal, outcome_rows=outcomes, threshold=3.0)
    assert rep["no_go"]["n"] == 2
    assert rep["no_go"]["idio"] == 1 and rep["no_go"]["vol"] == 1
    assert rep["by"]["source"]["decrypt"] == {"n": 1, "vol": 1, "dir": 1, "idio": 1}
    assert rep["by"]["source"]["google_news_metals"]["vol"] == 0
    assert rep["by"]["layer"]["2"]["idio"] == 1
    assert rep["by"]["layer"]["3"]["n"] == 1


def test_summarize_normalizes_phase_case_and_dedups_outcomes():
    journal = [
        _jrow("a", "decrypt", 2, event_phase="realized"),
        _jrow("b", "decrypt", 2, event_phase="REALIZED"),
    ]
    outcomes = [
        _orow("a", 10.0, -2.0, 8.0, 7.5),
        _orow("a", 1.0, -0.5, 0.2, 0.1),    # повторный resolve той же карточки — берём последний
        _orow("b", 1.0, -0.5, 0.2, 0.1),
    ]
    rep = C.summarize(journal_rows=journal, outcome_rows=outcomes, threshold=3.0)
    assert rep["no_go"]["n"] == 2                       # 2 карточки, не 3 строки
    assert rep["no_go"]["vol"] == 0                     # последний outcome 'a' чистый
    assert list(rep["by"]["event_phase"].keys()) == ["REALIZED"]
    assert rep["by"]["event_phase"]["REALIZED"]["n"] == 2


def test_gate_attribution_counts_chief_no_go_fields():
    journal = [
        _jrow("a", "decrypt", 2, in_price="yes", surprise="none"),
        _jrow("b", "decrypt", 2, in_price="partial", surprise="timing"),
        _jrow("w", "decrypt", 2, verdict="WATCH"),
    ]
    rep = C.summarize(journal_rows=journal, outcome_rows=[], threshold=3.0)
    assert rep["gate_attribution"]["verdicts"] == {"NO_GO": 2, "WATCH": 1}
    assert rep["gate_attribution"]["chief_no_go_in_price"] == {"yes": 1, "partial": 1}


def test_render_text_smoke():
    rep = C.summarize(journal_rows=[_jrow("a", "decrypt", 2)],
                      outcome_rows=[_orow("a", 10.0, -2.0, 8.0, 7.5)], threshold=3.0)
    text = C.render_text(rep)
    assert "missed NO_GO по source" in text
    assert "decrypt" in text
