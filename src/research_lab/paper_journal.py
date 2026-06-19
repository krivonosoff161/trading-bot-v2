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
    _upsert_paper_outcome(private_root, outcome)
    return path


def _upsert_paper_outcome(private_root: Path, outcome: PaperTradeOutcome) -> None:
    """Best-effort SQLite aggregate; JSONL remains the append-only source."""
    try:
        from src.research_lab.state_db import connect, default_db_path, init_db
        conn = connect(default_db_path(Path(private_root)))
        init_db(conn)
        payload = outcome.to_dict()
        conn.execute(
            """INSERT INTO paper_outcomes(
                 trade_id, setup_id, candidate_id, symbol, timeframe, family, direction,
                 state, reason, outcome, opened_at, closed_at, net_pct, r_multiple,
                 data_fingerprint, params_hash, recorded_at, payload_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(trade_id) DO UPDATE SET
                 state=excluded.state, reason=excluded.reason, outcome=excluded.outcome,
                 closed_at=excluded.closed_at, net_pct=excluded.net_pct,
                 r_multiple=excluded.r_multiple, recorded_at=excluded.recorded_at,
                 payload_json=excluded.payload_json""",
            (
                outcome.trade_id, outcome.setup_id, outcome.candidate_id,
                outcome.symbol, outcome.timeframe, outcome.family, outcome.direction,
                outcome.state, outcome.reason, outcome.outcome, outcome.opened_at,
                outcome.closed_at, float(outcome.net_pct), float(outcome.r_multiple),
                outcome.data_fingerprint, outcome.params_hash, outcome.recorded_at,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        status = "PAPER_RECORDED"
        conn.execute(
            "UPDATE farm_results SET paper_status=? WHERE candidate_id=?",
            (status, outcome.candidate_id),
        )
        # If this candidate came from the lifecycle exporter, use its request metadata
        # to stamp the original farm_result and unique_candidate too.
        req = _request_context(Path(private_root), outcome.candidate_id)
        metrics = req.get("metrics") if isinstance(req.get("metrics"), dict) else {}
        original_candidate = str(metrics.get("source_candidate_id") or "")
        run_id = Path(str(req.get("source_run_id") or "").replace("\\", "/")).name
        if run_id and original_candidate:
            conn.execute(
                "UPDATE farm_results SET paper_status=? WHERE run_id=? AND candidate_id=?",
                (status, run_id, original_candidate),
            )
        conn.commit()
        conn.close()
        uc_key = str(metrics.get("uc_key") or "")
        if uc_key:
            from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
            db = FarmTasksDB(tasks_db_path(Path(private_root)))
            try:
                db.set_unique_paper_status(uc_key, status)
            finally:
                db.close()
    except Exception:
        # The append-only JSONL write above is the durable paper journal. Aggregate
        # feedback is best-effort and must not make paper runtime fail.
        return


def _request_context(private_root: Path, candidate_id: str) -> dict[str, Any]:
    path = private_root / "hard_validation" / "requests" / f"{candidate_id}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


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
