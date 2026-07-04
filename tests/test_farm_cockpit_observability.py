# -*- coding: utf-8 -*-
"""Phase 0.4 — dashboard observability: farm activity (heartbeat/cycles/errors) + paper P&L."""
from __future__ import annotations

import json
import time
from pathlib import Path

from src.research_lab import farm_cockpit, farm_journal


def _write_paper_trades(root: Path, nets: list[float]) -> None:
    p = root / "paper" / "paper_trades.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        for i, net in enumerate(nets):
            fh.write(json.dumps({"trade_id": f"t{i}", "net_pct": net,
                                 "r_multiple": net / 1.0, "outcome": "tp" if net > 0 else "sl"}) + "\n")


class TestFarmActivity:
    def test_unavailable_without_cycles(self, tmp_path) -> None:
        sec = farm_cockpit._farm_activity_section(tmp_path)
        assert sec["available"] is False

    def test_heartbeat_and_skipped_stages(self, tmp_path) -> None:
        stages = {"worker": {"enabled": True, "critical": True},
                  "validation": {"enabled": False, "critical": True}}
        farm_journal.log_cycle(tmp_path, ts=time.time(), mode="apply",
                               result={"pivot": "work_available", "active_tasks": 4,
                                       "counters": {"sweeps": 2}},
                               stages=stages,
                               discovery={"status": "fresh", "age_seconds": 30, "count": 100})
        sec = farm_cockpit._farm_activity_section(tmp_path)
        assert sec["available"] is True
        assert sec["heartbeat_ok"] is True
        assert sec["last_pivot"] == "work_available"
        assert sec["skipped_stages"] == ["validation"]
        assert sec["discovery"]["status"] == "fresh"

    def test_stale_heartbeat_flagged(self, tmp_path) -> None:
        farm_journal.log_cycle(tmp_path, ts=time.time() - 7200, mode="apply",
                               result={"pivot": "blocked:no_eligible_tasks"})
        sec = farm_cockpit._farm_activity_section(tmp_path)
        assert sec["heartbeat_ok"] is False

    def test_errors_surfaced(self, tmp_path) -> None:
        farm_journal.log_cycle(tmp_path, ts=time.time(), mode="apply", result={"pivot": "x"})
        farm_journal.log_error(tmp_path, where="worker", error="boom", ts=time.time())
        sec = farm_cockpit._farm_activity_section(tmp_path)
        assert sec["error_count"] == 1
        assert sec["recent_errors"][0]["where"] == "worker"


class TestPaperPnl:
    def test_unavailable_without_trades(self, tmp_path) -> None:
        assert farm_cockpit._paper_pnl_section(tmp_path)["available"] is False

    def test_aggregates_win_rate_and_net(self, tmp_path) -> None:
        _write_paper_trades(tmp_path, [2.0, -1.0, 3.0, -0.5])
        sec = farm_cockpit._paper_pnl_section(tmp_path)
        assert sec["available"] is True
        assert sec["n_trades"] == 4
        assert sec["wins"] == 2 and sec["losses"] == 2
        assert sec["win_rate"] == 0.5
        assert sec["net_sum_pct"] == 3.5


class TestBuildCockpit:
    def test_includes_new_sections(self, tmp_path) -> None:
        cockpit = farm_cockpit.build_cockpit(tmp_path)
        assert "farm_activity" in cockpit
        assert "paper_pnl" in cockpit
        # degrades cleanly on an empty root
        assert cockpit["farm_activity"]["available"] is False
        assert cockpit["paper_pnl"]["available"] is False


class TestHtmlSmoke:
    def test_renders_activity_and_pnl(self) -> None:
        from src.research_lab.dashboard_server import farm_cockpit_html
        cockpit = {
            "farm_activity": {"available": True, "heartbeat_ok": True,
                              "last_cycle_age_seconds": 12, "last_pivot": "work_available",
                              "last_mode": "apply", "skipped_stages": ["paper"],
                              "discovery": {"status": "fresh", "age_seconds": 30},
                              "recent_errors": [], "error_count": 0},
            "paper_pnl": {"available": True, "n_trades": 4, "wins": 2, "losses": 2,
                          "win_rate": 0.5, "avg_net_pct": 0.875, "net_sum_pct": 3.5,
                          "avg_r_multiple": 0.875},
        }
        html = farm_cockpit_html(cockpit)
        assert "heartbeat" in html
        assert "paper P&amp;L" in html
        assert "win_rate" in html
