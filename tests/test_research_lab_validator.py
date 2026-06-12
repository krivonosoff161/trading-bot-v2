# -*- coding: utf-8 -*-

from src.research_lab.validator import validate_candidate


def _good_metrics(**overrides) -> dict:
    metrics = {
        "n_trades": 40,
        "min_trades": 10,
        "win_rate": 0.55,
        "avg_net_pct": 0.8,
        "total_net_pct": 32.0,
        "profit_factor": 1.6,
        "max_drawdown_pct": 8.0,
        "best_trade_share": 0.2,
        "train_avg_net_pct": 0.9,
        "test_avg_net_pct": 0.6,
        "test_trades": 12,
        "stress_avg_net_pct": 0.7,
        "regime_breakdown": {
            "medium|up|normal": {"n_trades": 30, "avg_net_pct": 0.9, "win_rate": 0.6},
        },
    }
    metrics.update(overrides)
    return metrics


def test_forward_paper_when_all_gates_pass():
    result = validate_candidate(_good_metrics(), "PROMOTE_FOR_PRESSURE_TEST")

    assert result.status == "FORWARD_PAPER"
    assert result.reasons == ["passed_lite_validation"]
    assert "parameter_fragility_unknown" in result.risk_flags
    assert "paper-forward" in result.next_action


def test_reject_too_few_trades():
    result = validate_candidate(_good_metrics(n_trades=3), "REJECT")

    assert result.status == "REJECT"
    assert "too_few_trades" in result.reasons


def test_reject_negative_oos_without_strong_regime():
    metrics = _good_metrics(test_avg_net_pct=-0.4, regime_breakdown={})

    result = validate_candidate(metrics, "OBSERVE")

    assert result.status == "REJECT"
    assert "oos_not_positive" in result.reasons


def test_regime_specific_when_one_bucket_carries():
    metrics = _good_metrics(
        test_avg_net_pct=-0.1,
        regime_breakdown={
            "high|up|elevated": {"n_trades": 15, "avg_net_pct": 1.5, "win_rate": 0.7},
            "low|sideways|thin": {"n_trades": 20, "avg_net_pct": -0.8, "win_rate": 0.3},
        },
    )

    result = validate_candidate(metrics, "OBSERVE")

    assert result.status == "REGIME_SPECIFIC"
    assert any(r.startswith("strong_regime_bucket:high|up|elevated") for r in result.reasons)
    assert "regime" in result.next_action


def test_observe_on_soft_failures():
    metrics = _good_metrics(profit_factor=1.05, best_trade_share=0.5)

    result = validate_candidate(metrics, "OBSERVE")

    assert result.status == "OBSERVE"
    assert "weak_profit_factor" in result.reasons
    assert "single_trade_dominance" in result.reasons


def test_cost_stress_failure_is_recorded():
    metrics = _good_metrics(stress_avg_net_pct=-0.05)

    result = validate_candidate(metrics, "PROMOTE_FOR_PRESSURE_TEST")

    assert result.status == "OBSERVE"
    assert "dies_under_cost_stress" in result.reasons


def test_unknown_regime_buckets_do_not_qualify():
    metrics = _good_metrics(
        test_avg_net_pct=-0.1,
        regime_breakdown={
            "unknown|unknown|unknown": {"n_trades": 50, "avg_net_pct": 2.0, "win_rate": 0.8},
        },
    )

    result = validate_candidate(metrics, "OBSERVE")

    assert result.status == "REJECT"
