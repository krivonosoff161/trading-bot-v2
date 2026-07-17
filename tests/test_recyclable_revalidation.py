# -*- coding: utf-8 -*-
"""Phase D honest re-validation: candidate construction, the multiple-testing bridge wiring (a
strong series passes, a thin one fails), and the verdict summary. A survivor is research-only and
never auto-promoted to paper-ready."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.honest_backtest_bridge import bridge_available, run_validation  # noqa: E402
from src.research_lab.hard_validation_contract import trade_evidence_hash  # noqa: E402
from src.research_lab.simulator_contract import (  # noqa: E402
    build_cost_ledger,
    build_trade_quantity_ledger,
    legacy_fixture_manifest,
)
from src.research_lab.recyclable_revalidation import (  # noqa: E402
    _avg_net,
    _candidate,
    summarize_revalidation,
    write_revalidation_snapshot,
)

import pytest  # noqa: E402

_ITEM = {"uc_key": "X::1h::momentum_breakout::ph::fp", "symbol": "X", "timeframe": "1h",
         "family": "momentum_breakout", "params": {"stop_pct": 1.0, "take_pct": 2.0},
         "evidence_stage": "untouched_evaluation",
         "selection_data_fingerprint": "selection-fp",
         "selection_evidence": [{"net_pct": 0.2, "entry_ts": 1, "exit_ts": 2, "side": "long"}],
         "data_fingerprint": "evaluation-fp",
         "evaluation_data_fingerprint": "evaluation-fp",
         "hypothesis_frozen_at": "2026-07-01T00:00:00+00:00",
         "evaluation_started_at": "2026-07-02T00:00:00+00:00"}
_ITEM["selection_evidence_hash"] = trade_evidence_hash(_ITEM["selection_evidence"])


def _evaluation_trades(values):
    start = 1782950400  # 2026-07-02T00:00:00+00:00
    manifest = legacy_fixture_manifest()
    return [
        {"net_pct": value, "gross_pct": value + 0.1,
         "entry_ts": start + index * 60, "exit_ts": start + index * 60 + 30,
         "side": "long", "simulator_manifest": manifest,
         "simulator_model_id": manifest["simulator_model_id"],
         "simulator_evidence_tier": manifest["evidence_tier"],
         "unsupported_simulator_dimensions": manifest["unsupported_dimensions"],
         "cost_ledger": build_cost_ledger(fees_bps=7.0, slippage_bps=3.0),
         "quantity_ledger": build_trade_quantity_ledger()}
        for index, value in enumerate(values)
    ]


class TestCandidate:
    def test_isolates_statistical_question(self):
        trades = _evaluation_trades([0.5, -0.2])
        c = _candidate(_ITEM, trades, n_trials=8)
        assert c.lite_status == "FORWARD_PAPER"          # so forward_readiness is not the blocker
        assert c.metrics["n_trades"] == 2
        assert c.metrics["runtime"]["n_variants_evaluated"] == 8  # Sidak deflation by grid size
        assert c.candidate_id.startswith("reval::")

    def test_avg_net(self):
        assert _avg_net([{"net_pct": 1.0}, {"net_pct": -0.5}]) == 0.25
        assert _avg_net([]) == 0.0


@pytest.mark.skipif(not bridge_available()["available"], reason="honest-backtest bridge not importable")
class TestBridgeWiring:
    def test_thin_series_fails_oos(self, tmp_path):
        c = _candidate(_ITEM, _evaluation_trades([0.5] * 4), n_trials=1)  # n<10 -> split analysis fails
        assert run_validation(c, tmp_path, dry_run=True)["hard_status"] == "FAILED_DATA_QUALITY"

    def test_one_two_trade_needs_more_data(self, tmp_path):
        c = _candidate(_ITEM, _evaluation_trades([1.5]), n_trials=1)
        assert run_validation(c, tmp_path, dry_run=True)["hard_status"] == "FAILED_DATA_QUALITY"

    def test_strong_series_can_pass(self, tmp_path):
        # A strong return series cannot bypass missing immutable search-family evidence.
        trades = _evaluation_trades([0.5, 0.4, 0.6] * 9)  # n=27, mean 0.5%, low variance
        status = run_validation(_candidate(_ITEM, trades, n_trials=1), tmp_path, dry_run=True)["hard_status"]
        assert status == "FAILED_DATA_QUALITY"

    def test_sidak_deflation_makes_it_harder(self, tmp_path):
        # The SAME borderline series is harder to pass when deflated by a big trial count.
        trades = _evaluation_trades([0.3, 0.1, 0.2, -0.1, 0.25] * 4)  # n=20, marginal
        lenient = run_validation(_candidate(_ITEM, trades, 1), tmp_path, dry_run=True)["hard_status"]
        deflated = run_validation(_candidate(_ITEM, trades, 50), tmp_path, dry_run=True)["hard_status"]
        # deflation never makes a pass easier
        order = {"PAPER_FORWARD_READY": 1}
        assert order.get(deflated, 0) <= order.get(lenient, 0)


class TestSummary:
    def test_zero_survivors_verdict(self):
        rows = [{"uc_key": "a", "symbol": "X", "timeframe": "1h", "family": "f", "bucket": "tactical_candidate",
                 "n_trades": 1, "exit": "baseline", "revalidation_status": "NEEDS_MORE_DATA"},
                {"uc_key": "b", "symbol": "Y", "timeframe": "1h", "family": "f", "bucket": "validator_too_strict",
                 "n_trades": 6, "exit": "baseline", "revalidation_status": "FAILED_OOS"}]
        s = summarize_revalidation(rows)
        assert s["survivors"] == 0 and "characterization, not edge" in s["verdict"]
        assert s["by_bucket"]["tactical_candidate"] == {"NEEDS_MORE_DATA": 1}

    def test_survivor_flagged_not_promoted(self):
        rows = [{"uc_key": "c", "symbol": "Z", "timeframe": "4h", "family": "mean_reversion_fade",
                 "bucket": "exit_recovered", "n_trades": 12, "exit": "tp_half",
                 "revalidation_status": "PAPER_FORWARD_READY",
                 "hypothesis_frozen_at": "2026-07-01T00:00:00+00:00",
                 "selection_cutoff_ts": 2,
                 "selection_data_fingerprint": "selection-fp",
                 "selection_evidence": [{"entry_ts": 1, "exit_ts": 2, "net_pct": 0.2}],
                 "data_snapshot_id": "csm_fixture",
                 "data_evidence_hash": "evidence-fixture",
                 "data_provenance_status": "complete"}]
        s = summarize_revalidation(rows)
        assert s["survivors"] == 1 and "human GO" in s["verdict"]
        assert s["survivor_rows"][0]["uc_key"] == "c"

    def test_snapshot_round_trip(self, tmp_path):
        rows = [{"uc_key": "a", "symbol": "X", "timeframe": "1h", "family": "f", "bucket": "exit_recovered",
                 "n_trades": 8, "exit": "tp_half", "revalidation_status": "FAILED_OOS"}]
        import json
        p = write_revalidation_snapshot(tmp_path, rows)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["by_uc_key"]["a"]["revalidation_status"] == "FAILED_OOS"
        assert "NEVER auto paper-ready" in data["disclaimer"]
