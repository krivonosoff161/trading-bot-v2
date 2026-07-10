"""Paper-money accounting for research-only trade ledgers.

This module is a deterministic calculator for the operator-agreed paper account
model. It has no exchange, account, Telegram, env, provider, or order imports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

SCHEMA = "PaperMoneyModel.v1"

DEFAULT_DEPOSIT_USDT = 700.0
MIN_POSITION_MARGIN_USDT = 30.0
MAX_POSITION_MARGIN_USDT = 40.0
DEFAULT_POSITION_MARGIN_USDT = 35.0
DEFAULT_LEVERAGE = 3.0
MAX_LEVERAGE = 5.0


@dataclass(frozen=True)
class PaperMoneyModel:
    deposit_usdt: float = DEFAULT_DEPOSIT_USDT
    position_margin_usdt: float = DEFAULT_POSITION_MARGIN_USDT
    leverage: float = DEFAULT_LEVERAGE
    min_position_margin_usdt: float = MIN_POSITION_MARGIN_USDT
    max_position_margin_usdt: float = MAX_POSITION_MARGIN_USDT
    max_leverage: float = MAX_LEVERAGE
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA}")
        if self.deposit_usdt <= 0:
            raise ValueError("paper deposit must be positive")
        if not self.min_position_margin_usdt <= self.position_margin_usdt <= self.max_position_margin_usdt:
            raise ValueError("paper position margin is outside the agreed band")
        if self.leverage <= 0 or self.leverage > self.max_leverage:
            raise ValueError("paper leverage is outside the agreed cap")

    @property
    def notional_usdt(self) -> float:
        return round(self.position_margin_usdt * self.leverage, 6)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"notional_usdt": self.notional_usdt}


def default_paper_money_model() -> PaperMoneyModel:
    return PaperMoneyModel()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in ("", None) else default)
    except (TypeError, ValueError):
        return default


def paper_money_from_outcome(
    outcome: dict[str, Any] | None,
    *,
    model: PaperMoneyModel | None = None,
) -> dict[str, Any]:
    """Derive paper PnL from an observed unlevered net percentage.

    ``net_pct`` in the paper signal outcome is the price-path percent after the
    shared cost assumptions. Paper PnL applies the chosen margin and leverage to
    that percent, but still remains a research artifact, not account state.
    """
    model = model or default_paper_money_model()
    outcome = outcome or {}
    net_pct_value = _float(outcome.get("net_pct"))
    has_terminal_net = "net_pct" in outcome and outcome.get("net_pct") not in ("", None)
    pnl_usdt = round(model.notional_usdt * net_pct_value / 100.0, 6) if has_terminal_net else 0.0
    return {
        "schema": SCHEMA,
        "deposit_usdt": model.deposit_usdt,
        "position_margin_usdt": model.position_margin_usdt,
        "margin_band_usdt": [model.min_position_margin_usdt, model.max_position_margin_usdt],
        "leverage": model.leverage,
        "max_leverage": model.max_leverage,
        "notional_usdt": model.notional_usdt,
        "net_pct": net_pct_value if has_terminal_net else None,
        "pnl_usdt": pnl_usdt if has_terminal_net else None,
        "equity_after_usdt": round(model.deposit_usdt + pnl_usdt, 6) if has_terminal_net else None,
        "paper_only": True,
        "execution_allowed": False,
    }


def summarize_paper_money(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize independent what-if outcomes, never a shared account balance."""
    terminal = [
        row.get("paper_account") or {}
        for row in rows
        if isinstance(row.get("paper_account"), dict) and row.get("paper_account", {}).get("pnl_usdt") is not None
    ]
    pnl = [float(row.get("pnl_usdt") or 0.0) for row in terminal]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    model = default_paper_money_model()
    return {
        "schema": "paper_money_summary.v1",
        "model": model.to_dict(),
        "terminal_trades": len(pnl),
        "wins": len(wins),
        "losses": len(losses),
        "total_pnl_usdt": round(sum(pnl), 6),
        "avg_pnl_usdt": round(sum(pnl) / len(pnl), 6) if pnl else 0.0,
        "counterfactual": True,
        "account_state": False,
        "note": "independent research variants; do not interpret summed PnL as account equity",
        "paper_only": True,
        "execution_allowed": False,
    }
