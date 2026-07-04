import json
import sqlite3
from pathlib import Path

from src.research_lab.ready_strategy_catalog import (
    build_ready_strategy_catalog,
    catalog_snapshot_path,
)


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE farm_results (
            run_id TEXT, candidate_id TEXT, symbol TEXT, family TEXT, timeframe TEXT,
            avg_net_pct REAL, win_rate REAL, n_trades INTEGER, max_drawdown_pct REAL,
            hard_status TEXT, paper_status TEXT
        )"""
    )
    cur.execute(
        """CREATE TABLE candidates (
            run_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            family TEXT,
            params_json TEXT,
            PRIMARY KEY (run_id, candidate_id)
        )"""
    )
    rows = [
        (
            "R1",
            "C1",
            "BTC_USDT_SWAP",
            "momentum_breakout",
            "4h",
            2.2,
            0.62,
            30,
            12.0,
            "PAPER_FORWARD_READY",
            "forward_watch",
            {"lookback": 10, "stop_pct": 3, "take_pct": 8, "hold_bars": 4},
        ),
        (
            "R1",
            "C2",
            "ETH_USDT_SWAP",
            "mean_reversion_fade",
            "1h",
            -0.1,
            0.40,
            18,
            10.0,
            "PAPER_FORWARD_READY",
            "forward_watch",
            {"lookback": 5, "move_pct": 5, "stop_pct": 3, "take_pct": 8, "hold_bars": 4},
        ),
        (
            "R1",
            "C3",
            "SOL_USDT_SWAP",
            "momentum_breakout",
            "1h",
            9.0,
            0.80,
            100,
            5.0,
            "FAILED_OOS",
            "",
            {"lookback": 10, "stop_pct": 3, "take_pct": 8, "hold_bars": 4},
        ),
    ]
    for row in rows:
        *farm, params = row
        cur.execute("INSERT INTO farm_results VALUES (?,?,?,?,?,?,?,?,?,?,?)", farm)
        cur.execute(
            "INSERT INTO candidates VALUES (?,?,?,?)",
            (farm[0], farm[1], farm[3], json.dumps(params)),
        )
    conn.commit()
    conn.close()


def test_ready_strategy_catalog_filters_and_writes_private_snapshot(tmp_path):
    db = tmp_path / "strategy_lab.sqlite"
    _make_db(db)

    summary = build_ready_strategy_catalog(tmp_path, db)

    assert summary["records_loaded"] == 2
    assert summary["ready"] == 1
    assert summary["rejected_quality"] == 1
    assert summary["paper_only"] is True
    assert summary["execution_allowed"] is False

    snapshot = json.loads(catalog_snapshot_path(tmp_path).read_text(encoding="utf-8"))
    rows = snapshot["items"]
    ready = [row for row in rows if row["status"] == "ready_for_paper_runtime"]
    rejected = [row for row in rows if row["status"] == "rejected_quality"]
    assert len(ready) == 1
    assert ready[0]["ready_strategy_id"].startswith("ready_")
    assert ready[0]["setup_id"] == "setup-C1"
    assert ready[0]["execution_allowed"] is False
    assert rejected[0]["reasons"]
