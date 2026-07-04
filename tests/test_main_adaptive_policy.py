import ast
from pathlib import Path

from src.research_lab.main_adaptive_policy import build_policy, validate_advisor_policy


def _row(family: str = "early_tp_tactical", *, risk_pct: float = 2.5) -> dict:
    return {
        "source_signal_id": "sig_1",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "15m",
        "side": "long",
        "setup_family": family,
        "risk_pct": risk_pct,
        "exit_mode": "partial_be",
    }


def test_build_policy_selects_fast_tactical_profile():
    policy = build_policy(_row())

    assert policy.schema == "MainAdaptivePolicy.v1"
    assert policy.execution_profile == "fast_tactical_watch"
    assert policy.exit_profile == "early_tp_partial_be"
    assert policy.paper_only is True
    assert policy.execution_allowed is False
    assert "forward_lead:early_tp_tactical" in policy.reason_codes


def test_build_policy_penalizes_wide_risk():
    compact = build_policy(_row(risk_pct=2.0))
    wide = build_policy(_row(risk_pct=12.0))

    assert wide.confidence < compact.confidence
    assert "risk_too_wide" in wide.reason_codes


def test_advisor_policy_rejects_trading_numbers_and_execution():
    ok, problems = validate_advisor_policy(
        {
            "execution_profile": "fast_tactical_watch",
            "entry": 100.0,
            "stop_loss": 95.0,
            "execution_allowed": True,
        }
    )

    assert ok is False
    assert "execution_allowed_true" in problems
    assert any(problem.startswith("forbidden_fields:") for problem in problems)


def test_main_adaptive_policy_has_no_live_order_imports():
    path = Path("src/research_lab/main_adaptive_policy.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "main",
        "src.exchange",
        "src.exchange.okx_client",
        "src.utils.telegram",
        "dotenv",
        "ccxt",
        "hmac",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & forbidden)
