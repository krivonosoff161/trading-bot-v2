# -*- coding: utf-8 -*-
"""
test_nogo_audit_buckets.py — раскладка NO_GO по бакетам (без сети).

Аудит 11.06: self-baseline активы (BTC/CL/XAU) попадали в directional-промахи,
завышая «упущенное»; chief-error карточки считались обычными NO_GO.
"""
import datetime as dt
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.scout.nogo_audit import classify_bucket  # noqa: E402

NOW = dt.datetime(2026, 6, 11, tzinfo=dt.timezone.utc)


def _jrow(**kw):
    row = {"card_id": "x", "ts_utc": "2026-06-01T00:00:00Z", "horizon_hours": 24,
           "verdict": "NO_GO", "okx_inst": "ETH-USDT-SWAP",
           "baseline_symbol": "BTC-USDT-SWAP"}
    row.update(kw)
    return row


def _outcome(**kw):
    o = {"scored": True, "ret_pct": 0.5, "mfe_long_pct": 1.0, "mae_long_pct": -1.0,
         "excess_pct": 0.2}
    o.update(kw)
    return o


def test_idio_requires_meaningful_baseline():
    # ETH vs BTC, excess 5% → реальный idio-промах
    assert classify_bucket(_jrow(), _outcome(ret_pct=6.0, excess_pct=5.0), NOW) == "MISSED_IDIO_MOVE"


def test_self_baseline_big_move_is_beta_blind_not_idio():
    j = _jrow(okx_inst="BTC-USDT-SWAP", baseline_symbol="BTC-USDT-SWAP")
    # старый формат outcome: excess фиктивный 0.0
    o = _outcome(ret_pct=-6.0, excess_pct=0.0, mae_long_pct=-7.0)
    assert classify_bucket(j, o, NOW) == "BETA_BLIND_MOVE"
    # новый формат: beta_blind=True, excess=None
    o2 = _outcome(ret_pct=-6.0, excess_pct=None, beta_blind=True, mae_long_pct=-7.0)
    assert classify_bucket(j, o2, NOW) == "BETA_BLIND_MOVE"


def test_market_beta_directional_still_separate():
    # ETH упал на 4%, но excess мал → бета рынка, не idio
    assert classify_bucket(_jrow(), _outcome(ret_pct=-4.0, excess_pct=-0.5,
                                             mae_long_pct=-4.5), NOW) == "MISSED_DIRECTIONAL_MOVE"


def test_volatility_wick_and_correct():
    assert classify_bucket(_jrow(), _outcome(mfe_long_pct=4.0), NOW) == "VOLATILE_BUT_NO_DIRECTION"
    assert classify_bucket(_jrow(), _outcome(), NOW) == "CORRECT_NO_GO"


def test_chief_error_cards_not_counted_as_normal_no_go():
    for gate in ("CHIEF_ERROR_FALLBACK", "CHIEF_ERROR_PENDING", "CHIEF_UNAVAILABLE"):
        j = _jrow(escalation_gate=gate)
        assert classify_bucket(j, _outcome(ret_pct=6.0, excess_pct=5.0), NOW) == "CHIEF_ERROR_UNRESOLVED"


def test_unscored_states_preserved():
    assert classify_bucket(_jrow(), {"scored": False}, NOW) == "MANUAL_OR_UNSCORED"
    fresh = _jrow(ts_utc="2026-06-11T00:00:00Z", horizon_hours=48)
    assert classify_bucket(fresh, None, NOW) == "NOT_MATURE"
