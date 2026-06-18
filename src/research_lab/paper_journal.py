# -*- coding: utf-8 -*-
"""Append-only paper-trade journal under the private research root."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.paper_contract import PaperTradeOutcome


def paper_dir(private_root: Path) -> Path:
    return Path(private_root) / "paper"


def paper_trades_path(private_root: Path) -> Path:
    return paper_dir(private_root) / "paper_trades.jsonl"


def load_seen_trade_ids(private_root: Path) -> set[str]:
    path = paper_trades_path(private_root)
    if not path.exists():
        return set()
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        trade_id = str(row.get("trade_id") or "")
        if trade_id:
            seen.add(trade_id)
    return seen


def append_paper_outcome(private_root: Path, outcome: PaperTradeOutcome) -> Path:
    path = paper_trades_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(outcome.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return path


def read_paper_outcomes(private_root: Path) -> list[dict[str, Any]]:
    path = paper_trades_path(private_root)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
