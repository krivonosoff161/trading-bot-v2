# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path

from src.research_lab.manual_farm_context import (
    active_farm_context_for_symbol,
    manual_farm_context_text,
    normalize_symbol_key,
)
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal


def _signal(
    *,
    signal_id: str = "sig_lab_pfr",
    source: str = "pfr_farm",
    symbol: str = "LAB_USDT_SWAP",
    status: str = "armed",
    created_at: float = 100.0,
    ready_strategy_id: str = "ready_lab_15m",
) -> PaperActionSignal:
    return PaperActionSignal(
        signal_id=signal_id,
        source=source,
        symbol=symbol,
        okx_inst_id=symbol.replace("_", "-"),
        timeframe="15m",
        side="long",
        setup_family="early_tp_tactical",
        entry_zone=[0.1, 0.11],
        stop_loss=0.09,
        invalidation_rule="breaks support",
        take_profit_plan=[{"price": 0.13, "size_frac": 1.0, "label": "tp1"}],
        max_hold_bars=9,
        max_hold_minutes=135,
        reason_now="validated tactical setup",
        validator_context={"ready_strategy_id": ready_strategy_id} if ready_strategy_id else {},
        status=status,
        created_at=created_at,
        expires_at=created_at + 900.0,
        ref_price=0.105,
        risk_pct=1.0,
        boundary_ts=1,
        data_fingerprint=f"fp_{signal_id}",
        dedup_key=f"{symbol}|15m|early_tp_tactical",
    )


def test_normalize_symbol_key_matches_manual_and_swap_forms():
    assert normalize_symbol_key("LAB-USDT") == "LAB_USDT"
    assert normalize_symbol_key("LAB-USDT-SWAP") == "LAB_USDT_SWAP"
    assert normalize_symbol_key("LAB_USDT_SWAP") == "LAB_USDT_SWAP"


def test_manual_context_surfaces_active_pfr_signal_for_same_pair(tmp_path):
    append_signal(tmp_path, _signal())

    text = manual_farm_context_text("LAB-USDT", private_root=tmp_path)

    assert "\u041a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 paper-\u0444\u0435\u0440\u043c\u044b" in text
    assert "PFR/pfr_farm" in text
    assert "LAB-USDT-SWAP" in text
    assert "15m" in text
    assert "LONG" in text
    assert "\u043d\u0435 \u043e\u0442\u043c\u0435\u043d\u044f\u0435\u0442" in text
    assert "\u043d\u0435 \u043f\u0440\u0438\u043a\u0430\u0437 \u043a \u0441\u0434\u0435\u043b\u043a\u0435" in text


def test_manual_context_ignores_closed_or_non_farm_signals(tmp_path):
    append_signal(tmp_path, _signal(signal_id="closed", status="closed_paper"))
    append_signal(tmp_path, _signal(signal_id="manual", source="manual", ready_strategy_id=""))

    assert active_farm_context_for_symbol("LAB-USDT", private_root=tmp_path) == []
    assert manual_farm_context_text("LAB-USDT", private_root=tmp_path) == ""


def test_manual_context_prioritizes_pfr_over_broad_farm(tmp_path):
    append_signal(
        tmp_path,
        _signal(
            signal_id="broad",
            source="farm",
            created_at=300.0,
            ready_strategy_id="",
        ),
    )
    append_signal(
        tmp_path,
        _signal(
            signal_id="pfr",
            source="pfr_farm",
            created_at=100.0,
            ready_strategy_id="ready",
        ),
    )

    items = active_farm_context_for_symbol("LAB-USDT-SWAP", private_root=tmp_path)

    assert [item.source_signal_id for item in items] == ["pfr", "broad"]


def test_manual_farm_context_has_no_execution_imports():
    path = Path("src/research_lab/manual_farm_context.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    forbidden = ("exchange", "order", "telegram", "auto_execute", "okx_client")
    assert not any(any(part in name for part in forbidden) for name in imports)
