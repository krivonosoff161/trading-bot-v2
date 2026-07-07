import pytest

from src.research_lab.paper_money_model import PaperMoneyModel, paper_money_from_outcome


def test_paper_money_model_converts_net_pct_to_usdt_pnl():
    account = paper_money_from_outcome({"net_pct": 2.0})

    assert account["schema"] == "PaperMoneyModel.v1"
    assert account["deposit_usdt"] == 700.0
    assert account["position_margin_usdt"] == 35.0
    assert account["leverage"] == 3.0
    assert account["notional_usdt"] == 105.0
    assert account["pnl_usdt"] == 2.1
    assert account["equity_after_usdt"] == 702.1
    assert account["paper_only"] is True
    assert account["execution_allowed"] is False


def test_paper_money_model_rejects_out_of_band_risk():
    with pytest.raises(ValueError):
        PaperMoneyModel(position_margin_usdt=100.0)
    with pytest.raises(ValueError):
        PaperMoneyModel(leverage=10.0)
