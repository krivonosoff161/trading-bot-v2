# -*- coding: utf-8 -*-
"""True-forward collector: pins a boundary at registration, counts only genuinely-new bars, matures on
forward trades, is stop-file/cap bounded, and has no execution path. Research-only, never paper-ready."""
import ast
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import true_forward as TF  # noqa: E402


def _write_candles(tmp_path, symbol, tf, n, *, start_i=0):
    d = tmp_path / "market_data" / tf
    d.mkdir(parents=True, exist_ok=True)
    step = 4 * 3600000
    rows = []
    for i in range(start_i, start_i + n):
        px = 100 + (i % 9) - 4
        rows.append({"ts": i * step, "date": "", "open": px, "high": px + 1.5, "low": px - 1.5,
                     "close": px + 0.2, "vol": 10.0})
    # overwrite the single file for this symbol/tf
    for old in d.glob(f"{symbol}_*_{tf}.json"):
        old.unlink()
    (d / f"{symbol}_{rows[0]['ts']}_{rows[-1]['ts']}_{tf}.json").write_text(json.dumps(rows), "utf-8")


def _seed_shadow(tmp_path, uc="u1", symbol="X", tf="4h"):
    d = tmp_path / "state" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / "shadow_forward.json").write_text(json.dumps({"by_uc_key": {uc: {
        "symbol": symbol, "timeframe": tf, "family": "mean_reversion_fade",
        "recovered_exit": "baseline", "params": {"stop_pct": 1.0, "take_pct": 2.0, "hold_bars": 3}}}}), "utf-8")
    (d / "setup_outcome_memory.json").write_text(json.dumps({"records": []}), "utf-8")


class TestBoundaryAndPending:
    def test_register_then_no_new_bars_is_pending(self, tmp_path):
        _write_candles(tmp_path, "X", "4h", 80)
        _seed_shadow(tmp_path, "u1", "X", "4h")
        TF.register(tmp_path, max_candidates=5)
        res = TF.collect_one(tmp_path, "u1")
        assert res["status"] == TF.STATUS_PENDING and res["new_bars"] == 0


class TestForwardOnGrowingFile:
    def test_new_bars_after_boundary_accumulate(self, tmp_path):
        _write_candles(tmp_path, "X", "4h", 80)
        _seed_shadow(tmp_path, "u1", "X", "4h")
        TF.register(tmp_path, max_candidates=5)
        # the file grows with genuinely new bars (ts strictly greater) -> collector must pick them up
        _write_candles(tmp_path, "X", "4h", 160)
        res = TF.collect_one(tmp_path, "u1")
        assert res["new_bars"] > 0
        assert res["status"] in (TF.STATUS_COLLECTING, TF.STATUS_MATURED)
        reg = TF._load_registry(tmp_path)
        assert reg["u1"]["paper_forward_ready"] is False
        # boundary advanced so a re-collect with no further growth is pending again
        assert TF.collect_one(tmp_path, "u1")["new_bars"] == 0


class TestStopFileAndCaps:
    def test_collect_once_respects_stop_file(self, tmp_path, monkeypatch):
        _write_candles(tmp_path, "X", "4h", 80)
        _seed_shadow(tmp_path, "u1", "X", "4h")
        TF.register(tmp_path, max_candidates=5)
        monkeypatch.setattr(TF, "is_stop_requested", lambda _root: True)
        assert TF.collect_once(tmp_path)["stopped"] is True


class TestNoExecutionPath:
    def test_no_forbidden_imports_and_never_paper_ready(self):
        src = (_ROOT / "src" / "research_lab" / "true_forward.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine", "auto_trade",
                     "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        assert 'paper_forward_ready": True' not in src and "paper_forward_ready=True" not in src
