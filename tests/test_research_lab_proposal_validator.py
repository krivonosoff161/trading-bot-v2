# -*- coding: utf-8 -*-

from src.research_lab.data_fingerprint import params_hash
from src.research_lab.proposal_schema import REJECTED, VALIDATED, coerce_proposal
from src.research_lab.proposal_validator import (
    compile_proposal,
    validate_and_mark,
    validate_proposal,
)
from src.research_lab.resource_policy import load_resource_policy
from src.research_lab.setup_outcome_memory import build_gate_index
from src.research_lab.timeframes import load_timeframe_profiles
from src.research_lab.universe import load_universe


def _ctx():
    return load_universe(), load_timeframe_profiles(), load_resource_policy()


def _proposal(**over):
    base = {
        "created_by": "rule_based",
        "hypothesis": "probe parameter neighbors for stability",
        "requested_timeframe": "15m",
        "setup_family": "momentum_breakout",
        "symbols": ["SOL_USDT_SWAP"],
        "parameter_grid": {"momentum_breakout": [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]},
        "max_variants": 6,
    }
    base.update(over)
    return coerce_proposal(base)


def _validate(p):
    u, tp, rp = _ctx()
    return validate_proposal(p, universe=u, timeframe_profiles=tp, resource_policy=rp)


def test_valid_proposal_passes():
    out = _validate(_proposal())
    assert out.status == VALIDATED
    assert out.reason_codes == []


def test_oversized_variants_rejected():
    grid = [{"lookback": i + 2, "hold_bars": 5, "stop_pct": 8, "take_pct": 16} for i in range(40)]
    out = _validate(_proposal(parameter_grid={"momentum_breakout": grid}, max_variants=40))
    assert out.status == REJECTED
    assert "too_many_variants" in out.reason_codes


def test_1m_full_sweep_blocked():
    out = _validate(_proposal(requested_timeframe="1m"))
    assert out.status == REJECTED
    assert "one_minute_full_sweep_blocked" in out.reason_codes


def test_unsafe_wording_blocked():
    out = _validate(_proposal(hypothesis="this is guaranteed live-tradable profit, place order now"))
    assert out.status == REJECTED
    assert "unsafe_wording" in out.reason_codes


def test_unknown_symbol_and_family_rejected():
    out = _validate(_proposal(symbols=["GHOST_USDT_SWAP"]))
    assert "unknown_symbol" in out.reason_codes
    out2 = _validate(_proposal(setup_family="not_a_family"))
    assert "unknown_family" in out2.reason_codes


def _bad_cand(symbol, tf, family, params, **over):
    base = {"symbol": symbol, "timeframe": tf, "family": family, "params_hash": params_hash(params),
            "data_fingerprint": "fp", "decision": "REJECT", "validation_status": "REJECT",
            "hard_status": "", "n_trades": 20, "avg_net_pct": -0.5}
    base.update(over)
    return base


def test_known_bad_in_memory_rejects_when_all_variants_dead():
    # the exact params for the proposal's only (symbol, tf, family) variant are confirmed-bad
    params = {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}
    idx = build_gate_index([_bad_cand("SOL_USDT_SWAP", "15m", "momentum_breakout", params)])
    u, tp, rp = _ctx()
    out = validate_proposal(_proposal(), universe=u, timeframe_profiles=tp, resource_policy=rp,
                            memory_index=idx)
    assert out.status == REJECTED and "known_bad_in_memory" in out.reason_codes


def test_one_fresh_variant_keeps_proposal_alive():
    # one dead variant + one fresh variant -> not all-dead -> allowed (fresh worth testing)
    dead = {"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}
    fresh = {"lookback": 30, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}
    idx = build_gate_index([_bad_cand("SOL_USDT_SWAP", "15m", "momentum_breakout", dead)])
    u, tp, rp = _ctx()
    out = validate_proposal(_proposal(parameter_grid={"momentum_breakout": [dead, fresh]}),
                            universe=u, timeframe_profiles=tp, resource_policy=rp, memory_index=idx)
    assert out.status == VALIDATED and "known_bad_in_memory" not in out.reason_codes


def test_no_memory_index_is_backward_compatible():
    out = _validate(_proposal())  # memory_index=None default
    assert out.status == VALIDATED


def test_hold_too_long_for_timeframe_rejected():
    # a 15m setup held 300 bars (~75h) is past the 192-bar (~48h) horizon band
    grid = [{"lookback": 20, "hold_bars": 300, "stop_pct": 8, "take_pct": 16}]
    out = _validate(_proposal(requested_timeframe="15m", parameter_grid={"momentum_breakout": grid}))
    assert out.status == REJECTED and "wrong_horizon" in out.reason_codes


def test_sane_hold_for_timeframe_passes_horizon():
    out = _validate(_proposal(requested_timeframe="15m"))  # hold_bars=5, well within band
    assert "wrong_horizon" not in out.reason_codes


def test_reward_risk_below_2r_rejected_through_proposal():
    # governance: the executable schema gate enforces RR>=2 at the PROPOSAL boundary, not just
    # at the param level. take_pct=10 with stop_pct=8 is 1.25R (< 2R) -> rejected.
    grid = [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 10}]
    out = _validate(_proposal(parameter_grid={"momentum_breakout": grid}))
    assert out.status == REJECTED
    assert any("reward_risk_below_2r" in code for code in out.reason_codes)


def test_llm_proposal_cannot_carry_paper_forward_ready():
    # governance: a Proposal has no paper-promotion field; any such key is silently ignored.
    p = _proposal()
    assert not hasattr(p, "paper_forward_ready")
    p2 = coerce_proposal({**_proposal_payload(), "paper_forward_ready": True, "status": "VALIDATED"})
    assert not hasattr(p2, "paper_forward_ready")


def test_llm_cannot_invent_created_by():
    import pytest
    with pytest.raises(ValueError):
        coerce_proposal({**_proposal_payload(), "created_by": "auto_promoter"})


def _proposal_payload():
    return {
        "created_by": "llm_review",
        "hypothesis": "probe parameter neighbors",
        "requested_timeframe": "15m",
        "setup_family": "momentum_breakout",
        "symbols": ["SOL_USDT_SWAP"],
        "parameter_grid": {"momentum_breakout": [{"lookback": 20, "hold_bars": 5, "stop_pct": 8, "take_pct": 16}]},
    }


def test_disallowed_timeframe_rejected():
    out = _validate(_proposal(requested_timeframe="3h"))
    assert "disallowed_timeframe" in out.reason_codes


def test_output_boundary_violation():
    out = _validate(_proposal(private_notes="write results to /trading-bot-v2/configs/leak.json"))
    assert "output_boundary_violation" in out.reason_codes


def test_validate_and_mark_sets_status():
    u, tp, rp = _ctx()
    good = validate_and_mark(_proposal(), universe=u, timeframe_profiles=tp, resource_policy=rp)
    assert good.status == VALIDATED
    bad = validate_and_mark(_proposal(requested_timeframe="1m"), universe=u, timeframe_profiles=tp, resource_policy=rp)
    assert bad.status == REJECTED
    assert bad.rejection_reason


def test_compile_proposal_bounded():
    _, _, rp = _ctx()
    exp = compile_proposal(_proposal(), policy=rp)
    assert exp.families == ["momentum_breakout"]
    assert exp.symbols == ["SOL_USDT_SWAP"]
    assert exp.max_runs <= rp.max_variants_per_job
