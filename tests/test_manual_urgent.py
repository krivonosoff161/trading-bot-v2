from __future__ import annotations

from pathlib import Path

import pytest

from scripts.strategy_lab.enqueue_manual_urgent import enqueue_manual_urgent
from src.research_lab.farm_priority import PRIORITY_MANUAL_URGENT
from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path


def test_manual_urgent_is_first_and_paper_only(tmp_path: Path):
    root = tmp_path / "private"
    result = enqueue_manual_urgent(
        root,
        symbol="btc",
        timeframe="15m",
        reason="проверить резкий импульс",
        now=1_800_000_000.0,
    )
    assert result["created"] is True
    assert result["symbol"] == "BTC_USDT_SWAP"
    assert result["priority"] == PRIORITY_MANUAL_URGENT
    assert result["paper_only"] is True
    assert result["execution_allowed"] is False
    db = FarmTasksDB(tasks_db_path(root))
    try:
        event = db.unconsumed_events(limit=1)[0]
    finally:
        db.close()
    assert event["source"] == "manual_urgent"
    assert event["priority"] == PRIORITY_MANUAL_URGENT


def test_manual_urgent_validates_timeframe(tmp_path: Path):
    with pytest.raises(ValueError, match="timeframe"):
        enqueue_manual_urgent(
            tmp_path,
            symbol="BTC",
            timeframe="5m",
            reason="test",
            now=1_800_000_000.0,
        )
