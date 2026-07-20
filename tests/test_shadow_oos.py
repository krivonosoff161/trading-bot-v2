# -*- coding: utf-8 -*-
"""Bounded OOS / shadow-forward evaluation: held-out-tail pseudo-OOS, no look-ahead, 5 research-only
classes, never paper-ready, no execution path."""
import ast
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab import shadow_oos as SO  # noqa: E402
from src.research_lab.simulator_contract import (  # noqa: E402
    build_cost_ledger,
    build_trade_quantity_ledger,
    legacy_fixture_manifest,
)


def _trade(net, mfe=2.0, mae=1.0, ttm=2, tp_before_sl=None):
    return {"net_pct": net, "mfe_pct": mfe, "mae_pct": mae, "time_to_mfe": ttm, "tp_before_sl": tp_before_sl}


def _bound_trade(trade):
    manifest = legacy_fixture_manifest()
    return {
        **trade,
        "gross_pct": float(trade["net_pct"]) + 0.1,
        "simulator_manifest": manifest,
        "simulator_model_id": manifest["simulator_model_id"],
        "simulator_evidence_tier": manifest["evidence_tier"],
        "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
        "cost_ledger": build_cost_ledger(fees_bps=7.0, slippage_bps=3.0),
        "quantity_ledger": build_trade_quantity_ledger(),
    }


class TestMetrics:
    def test_empty(self):
        m = SO._metrics([], 50)
        assert m["n_trades"] == 0 and m["avg_net_pct"] == 0.0 and m["bar_count"] == 50

    def test_aggregates_and_gross(self):
        trades = [_trade(1.0, tp_before_sl=True), _trade(-2.0, tp_before_sl=False), _trade(3.0)]
        m = SO._metrics(trades, 60)
        assert m["n_trades"] == 3
        assert m["net_sum_pct"] == 2.0
        # gross = net_avg + per-trade cost (0.1pp)
        assert m["avg_gross_pct"] == round(2.0 / 3 + SO.COST_PCT_PER_TRADE, 4)
        assert m["tp_before_sl"] == 1 and m["sl_before_tp"] == 1
        assert m["win_rate"] == round(2 / 3, 4)


class TestClassify:
    def _m(self, n, net, gross):
        return {"n_trades": n, "avg_net_pct": net, "avg_gross_pct": gross}

    def test_underpowered_zero_and_thin(self):
        assert SO._classify({}, self._m(0, 0, 0), "")[0] == "shadow_underpowered"
        assert SO._classify({}, self._m(4, 1.0, 1.1), "")[0] == "shadow_underpowered"

    def test_failed_costs_vs_failed_oos(self):
        # net <= 0 but gross > 0 -> costs ate it
        assert SO._classify({}, self._m(20, -0.05, 0.05), "")[0] == "shadow_failed_costs"
        # net <= 0 and gross <= 0 -> no edge even gross
        assert SO._classify({}, self._m(20, -0.5, -0.4), "")[0] == "shadow_failed_oos"

    def test_survived_requires_bridge_pass_and_is_sign(self):
        is_pos = {"avg_net_pct": 1.0}
        assert SO._classify(is_pos, self._m(20, 1.5, 1.6), "PAPER_FORWARD_READY")[0] == "shadow_survived"
        # positive net but bridge not a pass -> noise floor
        assert SO._classify(is_pos, self._m(20, 1.5, 1.6), "REGIME_ONLY")[0] == "shadow_noise_floor"
        # bridge pass but IS sign was negative -> noise floor (not IS-consistent)
        assert SO._classify({"avg_net_pct": -1.0}, self._m(20, 1.5, 1.6),
                            "PAPER_FORWARD_READY")[0] == "shadow_noise_floor"


def test_oos_bridge_builds_content_bound_untouched_epoch(monkeypatch):
    captured = {}

    def fake_run_validation(candidate, output_root, dry_run):
        captured["candidate"] = candidate
        return {"hard_status": "PAPER_FORWARD_READY"}

    monkeypatch.setattr(
        "src.research_lab.honest_backtest_bridge.run_validation", fake_run_validation
    )
    selection = [
        {"entry_ts": 1_700_000_000_000, "exit_ts": 1_700_000_060_000,
         "side": "long", "net_pct": 0.2}
    ]
    evaluation = [
        _bound_trade({"entry_ts": 1_700_000_120_000 + i * 60_000,
         "exit_ts": 1_700_000_150_000 + i * 60_000,
         "side": "long", "net_pct": 0.3})
        for i in range(10)
    ]
    status = SO._oos_bridge_status(
        {"symbol": "X", "timeframe": "1h", "family": "f", "exit": "baseline", "params": {},
         "hypothesis_frozen_at": "2023-11-14T22:14:00+00:00",
         "selection_cutoff_ts": 1_700_000_100_000,
         "selection_data_fingerprint": "sha256:selection",
         "selection_evidence": selection},
        selection, evaluation, 1,
    )
    epoch = captured["candidate"].metrics["validation_epoch"]
    assert status == "PAPER_FORWARD_READY"
    assert epoch["evidence_stage"] == "untouched_evaluation"
    assert epoch["selection_evidence"]
    assert epoch["selection_evidence_hash"] != epoch["evaluation_evidence_hash"]
    assert epoch["hypothesis_frozen_at"] < epoch["evaluation_started_at"]


def test_oos_bridge_fails_closed_when_selection_slice_has_no_trades():
    evaluation = [
        {"entry_ts": 1_700_000_120_000 + i * 60_000,
         "exit_ts": 1_700_000_150_000 + i * 60_000,
         "side": "long", "net_pct": 0.3}
        for i in range(10)
    ]
    status = SO._oos_bridge_status(
        {"symbol": "X", "timeframe": "1h", "family": "f", "exit": "baseline", "params": {}},
        [], evaluation, 1,
    )
    assert status == "NEEDS_MORE_DATA"


def test_oos_bridge_rejects_missing_registry_freeze_provenance():
    selection = [{"entry_ts": 1, "exit_ts": 2, "side": "long", "net_pct": 0.2}]
    evaluation = [
        {"entry_ts": 1_700_000_120_000 + i * 60_000,
         "exit_ts": 1_700_000_150_000 + i * 60_000,
         "side": "long", "net_pct": 0.3}
        for i in range(10)
    ]
    assert SO._oos_bridge_status(
        {"symbol": "X", "timeframe": "1h", "family": "f", "exit": "baseline", "params": {}},
        selection, evaluation, 1,
    ) == "NEEDS_MORE_DATA"


class TestCollectDedup:
    def test_dedup_same_signature(self, tmp_path):
        d = tmp_path / "state" / "derived"
        d.mkdir(parents=True)
        (d / "shadow_forward.json").write_text(json.dumps({"by_uc_key": {
            "uc1": {"symbol": "X_SWAP", "timeframe": "4h", "family": "mean_reversion_fade",
                    "recovered_exit": "hold_long", "params": {"stop_pct": 1}}}}), encoding="utf-8")
        (d / "oi_family_research.json").write_text(json.dumps({"rows": [
            {"symbol": "X_SWAP", "timeframe": "4h", "family": "mean_reversion_fade",
             "hard_status": "PAPER_FORWARD_READY"},  # same sym/tf/family but exit=baseline -> distinct
            {"symbol": "Y_SWAP", "timeframe": "4h", "family": "oi_price_quadrant",
             "hard_status": "FAILED_OOS"},  # not honest-passed -> excluded
        ]}), encoding="utf-8")
        cands = SO.collect_candidates(tmp_path)
        syms = sorted((c["symbol"], c["exit"]) for c in cands)
        assert syms == [("X_SWAP", "baseline"), ("X_SWAP", "hold_long")]  # Y excluded, no false merge


class TestEvaluateNoLookAhead:
    def _enriched(self, tmp_path, symbol="X", tf="4h", n=120):
        d = tmp_path / "market_data" / tf
        d.mkdir(parents=True, exist_ok=True)
        step = 4 * 3600000
        rows = []
        for i in range(n):
            px = 100 + (i % 9) - 4
            rows.append({"ts": i * step, "date": "", "open": px, "high": px + 1.5, "low": px - 1.5,
                         "close": px + 0.2, "vol": 10.0, "oi": 1000.0 + (i % 5) * 10})
        (d / f"{symbol}_{rows[0]['ts']}_{rows[-1]['ts']}_{tf}.json").write_text(json.dumps(rows), "utf-8")

    def test_research_only_and_oos_entries_in_tail(self, tmp_path):
        self._enriched(tmp_path, "X", "4h", 120)
        cand = {"uc_key": "u", "symbol": "X", "timeframe": "4h", "family": "mean_reversion_fade",
                "exit": "baseline", "params": {"stop_pct": 1.0, "take_pct": 2.0, "hold_bars": 3},
                "source": "test"}
        res = SO.evaluate_candidate(tmp_path, cand, n_trials=1, oos_frac=0.35)
        assert res.get("paper_forward_ready") is False
        if "outcome_class" in res:
            assert res["outcome_class"] in {"shadow_survived", "shadow_failed_costs", "shadow_failed_oos",
                                            "shadow_underpowered", "shadow_noise_floor"}
            # OOS window is the held-out tail; in/oos bar split must sum sensibly
            assert res["in_sample"]["bar_count"] + res["oos"]["bar_count"] == 120


class TestNoExecutionPath:
    def test_no_forbidden_imports_or_paper_ready_true(self):
        src = (_ROOT / "src" / "research_lab" / "shadow_oos.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = ("okx_client", "ccxt", "telegram", "order_exec", "live_engine",
                     "auto_trade", "credential", "dotenv")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), f"forbidden import: {mod}"
        # never sets paper_forward_ready True anywhere
        assert "paper_forward_ready\": True" not in src and "paper_forward_ready=True" not in src
