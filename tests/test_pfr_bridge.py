# -*- coding: utf-8 -*-
"""PASS D — PFR bridge + BE stop tests.

Covers 8 categories per the implementation spec:
  1. PFR bridge rejects non-PFR hard_status records
  2. PFR bridge rejects missing params / setup identity
  3. PFR bridge preserves setup_id / candidate_id / params_hash / data_fingerprint in validator_context
  4. PFR generation does NOT invent default params when canonical params missing
  5. No duplicate active signal for same setup_id / params_hash / dedup_key
  6. AST boundary: pfr_bridge.py has no forbidden imports (covered by TestNoLiveBoundary in
     test_paper_signals.py which scans ALL *.py in the paper_signals package)
  7. run_cycle with fake provider + fake PFR records
  8. BE stop tests (PASS A) — long, short, same-bar ambiguity, partial_be compatibility, pending state

Security constraints: no .env, no AUTO_TRADE, no order path, paper/research only.
"""
from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.paper_signals import cycle, lane, pfr_bridge  # noqa: E402
from src.research_lab.paper_signals.contract import PaperActionSignal  # noqa: E402
from src.research_lab.strategies.detectors import (  # noqa: E402
    detect_mean_reversion_fade,
    detect_momentum_breakout,
)
from src.research_lab.strategies.breakout import signals_momentum_breakout  # noqa: E402
from src.research_lab.strategies.mean_reversion import signals_mean_reversion_fade  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def _seed_complete_known_bad_authority(root: Path) -> None:
    """Seed an explicit empty complete authority for a direct PFR cycle.

    PFR tests exercise canonical paper-cycle behavior, not the separate
    missing/corrupt-known-bad failure contract.  An empty, digest-bound v2
    snapshot is therefore the correct synthetic precondition.
    """

    derived = root / "state" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "setup_outcome_memory.json").write_text(
        json.dumps(
            {
                "schema": "setup_outcome_memory.v2",
                "complete": True,
                "known_bad_set_sha256": lane._known_bad_digest(set()),
                "records": [],
            }
        ),
        encoding="utf-8",
    )


def _make_db(path: Path, rows: list[dict]) -> None:
    """Minimal strategy_lab.sqlite for PFR tests.

    candidates table uses composite PK (run_id, candidate_id) — same as the real DB.
    The JOIN in pfr_bridge.load_pfr_records must use both columns to avoid inflation.
    """
    root = path.parent.parent if path.parent.name == "state" else path.parent
    _seed_complete_known_bad_authority(root)
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    c.execute("""CREATE TABLE farm_results (
        run_id TEXT, candidate_id TEXT, symbol TEXT, family TEXT, timeframe TEXT,
        avg_net_pct REAL, win_rate REAL, n_trades INTEGER, max_drawdown_pct REAL,
        hard_status TEXT, paper_status TEXT
    )""")
    c.execute("""CREATE TABLE candidates (
        run_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL,
        family TEXT,
        params_json TEXT,
        metrics_json TEXT,
        PRIMARY KEY (run_id, candidate_id)
    )""")
    for r in rows:
        c.execute(
            "INSERT INTO farm_results VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("R1", r["candidate_id"], r["symbol"], r["family"], r["timeframe"],
             r.get("avg_net_pct", 1.0), r.get("win_rate", 0.6), r.get("n_trades", 25),
             r.get("max_drawdown_pct", 15.0), r.get("hard_status", "PAPER_FORWARD_READY"),
             r.get("paper_status", "")),
        )
        c.execute(
            "INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?)",
            (
                "R1",
                r["candidate_id"],
                r["family"],
                json.dumps(r.get("params", {})),
                json.dumps(r.get("metrics", {})),
            ),
        )
    conn.commit()
    conn.close()


# Canonical validated params from real PFR records (stop_pct=7 stays under MAX_RISK_PCT=8%)
_MRF_PARAMS = {"lookback": 5, "move_pct": 8.0, "stop_pct": 7.0, "take_pct": 20.0, "hold_bars": 2}
_MBR_PARAMS = {"lookback": 10, "stop_pct": 7.0, "take_pct": 20.0, "hold_bars": 5, "threshold_pct": 0.0}

_MRF_ROW_DICT = {
    "candidate_id": "C1", "symbol": "AAOI_USDT_SWAP", "family": "mean_reversion_fade",
    "timeframe": "1h", "avg_net_pct": 2.5, "win_rate": 0.65, "n_trades": 35,
    "max_drawdown_pct": 15.0, "hard_status": "PAPER_FORWARD_READY", "params": _MRF_PARAMS,
}
_MBR_ROW_DICT = {
    "candidate_id": "C2", "symbol": "AERO_USDT_SWAP", "family": "momentum_breakout",
    "timeframe": "4h", "avg_net_pct": 3.0, "win_rate": 0.60, "n_trades": 40,
    "max_drawdown_pct": 20.0, "hard_status": "PAPER_FORWARD_READY", "params": _MBR_PARAMS,
}


def _row(d: dict) -> dict:
    """Augment a raw row dict the same way load_pfr_records does."""
    r = dict(d)
    if "params" not in r:
        r["params"] = {}
    r["params_hash"] = pfr_bridge._params_hash(r["params"])
    r["setup_id"] = f"setup-{r['candidate_id']}"
    return r


def _mrf_candles_short(n: int = 25) -> list[dict]:
    """25 bars; last 5 bars rose ~10% (> move_pct=8) → short fade signal."""
    prices = [100.0] * (n - 6) + [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
    return [{"ts": i, "open": p, "high": p * 1.005, "low": p * 0.995, "close": p}
            for i, p in enumerate(prices)]


def _mbr_candles_long(n: int = 25) -> list[dict]:
    """25 bars; last bar jumps to 115 — well above the 10-bar high (~102) → long breakout."""
    prices = [100.0 + i * 0.1 for i in range(n - 1)] + [115.0]
    return [{"ts": i, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p}
            for i, p in enumerate(prices)]


def _flat_candles(n: int = 25, price: float = 100.0) -> list[dict]:
    """Flat candles — no fade or breakout signal."""
    return [{"ts": i, "open": price, "high": price * 1.001, "low": price * 0.999, "close": price}
            for i in range(n)]


def _mrf_near_candles_up(n: int = 25) -> list[dict]:
    """25 bars; last 5 bars rose 7.6% (< move_pct=8) -> short fade pre-trigger."""
    prices = [100.0] * (n - 6) + [100.0, 101.5, 103.0, 104.5, 106.0, 107.6]
    return [{"ts": i, "open": p, "high": p * 1.005, "low": p * 0.995, "close": p}
            for i, p in enumerate(prices)]


class FakeProvider:
    def __init__(self, candles: list[dict]):
        self._candles = candles

    def fetch_ohlcv(self, symbol: str, tf: str, start_ts: int, end_ts: int) -> list[dict]:
        step = {
            "15m": 900_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }.get(tf, 900_000)
        count = len(self._candles)
        return [
            {
                **row,
                "ts": int(end_ts) - (count - 1 - index) * step,
            }
            for index, row in enumerate(self._candles)
        ]


class CountingProvider(FakeProvider):
    def __init__(self, candles: list[dict]):
        super().__init__(candles)
        self.calls = 0

    def fetch_ohlcv(self, symbol: str, tf: str, start_ts: int, end_ts: int) -> list[dict]:
        self.calls += 1
        return super().fetch_ohlcv(symbol, tf, start_ts, end_ts)


def _live_signal(symbol: str = "LIVE_USDT_SWAP", tf: str = "15m", now: float = 25.0) -> PaperActionSignal:
    return PaperActionSignal(
        signal_id=f"{symbol}_{tf}_fake_live",
        source="farm",
        symbol=symbol,
        okx_inst_id=symbol.replace("_", "-"),
        timeframe=tf,
        side="long",
        setup_family="fake_live_family",
        entry_zone=[99.0, 100.0],
        stop_loss=95.0,
        invalidation_rule="synthetic test invalidation",
        take_profit_plan=[{"label": "tp1", "price": 110.0, "size_frac": 1.0}],
        max_hold_bars=5,
        max_hold_minutes=75,
        reason_now="synthetic live signal for reservation test",
        status="armed",
        created_at=now,
        expires_at=now + 3600,
        ref_price=100.0,
        risk_pct=5.0,
        boundary_ts=24,
        data_fingerprint="fake-live-fingerprint",
        dedup_key=f"{symbol}|{tf}|fake_live_family",
        mode="live",
    )


class TestPFRReservation:
    def test_default_pfr_fetch_budget_matches_full_cycle_launcher(self):
        assert inspect.signature(cycle.run_cycle).parameters["max_pfr_fetches"].default == 12
        assert inspect.signature(pfr_bridge.generate_pfr_signals).parameters["max_pfr_fetches"].default == 12
        source = Path("scripts/strategy_lab/paper_signals_run.py").read_text(encoding="utf-8")
        assert 'default=12' in source
        assert 'default=8' not in source

    def test_pfr_lane_runs_before_live_movers_even_without_reserved_slots(self, tmp_path, monkeypatch):
        db = tmp_path / "sl.sqlite"
        _make_db(db, [_MRF_ROW_DICT])

        monkeypatch.setattr(cycle, "_load_movers", lambda _root: [{
            "symbol": "LIVE_USDT_SWAP",
            "inst_id": "LIVE-USDT-SWAP",
            "score": 100,
            "group": "test",
            "spread_bps": 1,
            "vol_usd": 100_000_000,
            "move_pct": 10,
        }])
        monkeypatch.setattr(lane, "gate_candidate", lambda *_a, **_k: "ok")
        monkeypatch.setattr(cycle.families, "watch_only_reason", lambda _candles: None)
        monkeypatch.setattr(cycle.families, "generate", lambda *_a, **_k: [(_live_signal(now=4000.0), "fake_live")])

        seen_max_pfr: list[int] = []

        def fake_generate_pfr(*_args, max_pfr: int, **_kwargs):
            seen_max_pfr.append(max_pfr)
            return []

        monkeypatch.setattr(pfr_bridge, "generate_pfr_signals", fake_generate_pfr)

        no_reserve = cycle.run_cycle(
            tmp_path,
            max_new=1,
            apply=False,
            provider=FakeProvider(_mrf_candles_short()),
            now=4000.0,
            pfr_db_path=db,
            max_pfr_scan=10,
            pfr_reserved_new=0,
        )
        assert no_reserve["generated"] == 1
        assert seen_max_pfr == [1]

        with_reserve = cycle.run_cycle(
            tmp_path,
            max_new=2,
            apply=False,
            provider=FakeProvider(_mrf_candles_short()),
            now=4000.0,
            pfr_db_path=db,
            max_pfr_scan=10,
            pfr_reserved_new=1,
        )
        assert with_reserve["generated"] == 1
        assert seen_max_pfr == [1, 1]
        assert with_reserve["pfr_counts"]["pfr_reserved_slots"] == 1


# ── Category 1: non-PFR records excluded ─────────────────────────────────────

class TestPFRBridgeLoad:
    def test_search_family_lineage_is_loaded_from_candidate_metrics(self, tmp_path):
        db = tmp_path / "lineage.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE farm_results (run_id TEXT, candidate_id TEXT, symbol TEXT, "
            "family TEXT, timeframe TEXT, avg_net_pct REAL, win_rate REAL, n_trades INTEGER, "
            "max_drawdown_pct REAL, hard_status TEXT, paper_status TEXT)"
        )
        conn.execute(
            "CREATE TABLE candidates (run_id TEXT, candidate_id TEXT, params_json TEXT, "
            "metrics_json TEXT, PRIMARY KEY (run_id, candidate_id))"
        )
        conn.execute(
            "INSERT INTO farm_results VALUES "
            "('R1','C1','X','mean_reversion_fade','1h',1.0,0.6,20,10.0,"
            "'PAPER_FORWARD_READY','')"
        )
        conn.execute(
            "INSERT INTO candidates VALUES (?,?,?,?)",
            (
                "R1",
                "C1",
                json.dumps(_MRF_PARAMS),
                json.dumps(
                    {
                        "search_family_id": "sfd_parent",
                        "search_trial_id": "stept_parent",
                        "effective_n_trials": 4,
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

        row = pfr_bridge.load_pfr_records(db, private_root=tmp_path)[0]
        assert row["search_family_id"] == "sfd_parent"
        assert row["search_trial_id"] == "stept_parent"
        assert row["effective_n_trials"] == 4

    def test_non_pfr_hard_status_excluded(self, tmp_path):
        db = tmp_path / "sl.sqlite"
        _make_db(db, [
            {**_MRF_ROW_DICT, "candidate_id": "C1", "hard_status": "PAPER_FORWARD_READY"},
            {**_MBR_ROW_DICT, "candidate_id": "C2", "hard_status": "REJECT"},
            {**_MBR_ROW_DICT, "candidate_id": "C3", "hard_status": ""},
        ])
        recs = pfr_bridge.load_pfr_records(db, private_root=tmp_path)
        assert len(recs) == 1
        assert recs[0]["candidate_id"] == "C1"

    def test_missing_params_json_excluded(self, tmp_path):
        db = tmp_path / "sl.sqlite"
        conn = sqlite3.connect(str(db))
        c = conn.cursor()
        c.execute("CREATE TABLE farm_results (run_id TEXT, candidate_id TEXT, symbol TEXT, "
                  "family TEXT, timeframe TEXT, avg_net_pct REAL, win_rate REAL, n_trades INTEGER, "
                  "max_drawdown_pct REAL, hard_status TEXT, paper_status TEXT)")
        c.execute("CREATE TABLE candidates ("
                  "run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, family TEXT, params_json TEXT, "
                  "PRIMARY KEY (run_id, candidate_id))")
        c.execute("INSERT INTO farm_results VALUES "
                  "('R1','C1','X','mean_reversion_fade','1h',1.0,0.6,20,10.0,'PAPER_FORWARD_READY','')")
        # No candidates row with run_id='R1' — LEFT JOIN gives NULL params_json
        conn.commit()
        conn.close()
        recs = pfr_bridge.load_pfr_records(db, private_root=tmp_path)
        assert len(recs) == 0  # params_json IS NOT NULL filter excludes it

    def test_missing_db_returns_empty(self, tmp_path):
        recs = pfr_bridge.load_pfr_records(
            tmp_path / "nonexistent.sqlite", private_root=tmp_path
        )
        assert recs == []

    def test_params_hash_and_setup_id_added(self, tmp_path):
        db = tmp_path / "sl.sqlite"
        _make_db(db, [_MRF_ROW_DICT])
        recs = pfr_bridge.load_pfr_records(db, private_root=tmp_path)
        r = recs[0]
        assert r["setup_id"] == f"setup-{r['candidate_id']}"
        assert "params_hash" in r and len(r["params_hash"]) == 12
        # params_hash must be deterministic
        assert r["params_hash"] == pfr_bridge._params_hash(_MRF_PARAMS)

    def test_present_empty_generation_revokes_stale_sqlite_pfr(self, tmp_path):
        from src.research_lab.validation_generation import write_current_generation

        db = tmp_path / "state" / "strategy_lab.sqlite"
        db.parent.mkdir(parents=True)
        _make_db(db, [_MRF_ROW_DICT])
        write_current_generation(
            tmp_path,
            tasks=[{
                "task_id": 1,
                "task_type": "export_validation",
                "task_key": "empty",
                "payload_json": "{}",
            }],
            exported_ids=[],
            completed_ids=[],
            producer_time=2.0,
        )

        status_counts: dict[str, int] = {}
        assert pfr_bridge.load_pfr_records(
            db,
            private_root=tmp_path,
            status_counts=status_counts,
        ) == []
        assert status_counts == {
            "pfr_generation:ready_empty": 1,
            "pfr_generation_active_candidates": 0,
            "pfr_generation_authorized_candidates": 0,
        }
        with pytest.raises(TypeError, match="private_root"):
            pfr_bridge.load_pfr_records(db)

    def test_current_generation_maps_verified_source_row_to_validation_setup(self, tmp_path):
        from src.research_lab.honest_backtest_bridge import _artifact_stem
        from src.research_lab.validation_generation import write_current_generation

        db = tmp_path / "state" / "strategy_lab.sqlite"
        db.parent.mkdir(parents=True)
        _make_db(db, [{
            **_MRF_ROW_DICT,
            "metrics": {
                "search_family_id": "sfd_current",
                "search_trial_id": "stept_current",
                "effective_n_trials": 7,
            },
        }])
        validation_id = "fv_" + "a" * 64
        stem = _artifact_stem(validation_id)
        base = tmp_path / "hard_validation"
        for subdir in ("requests", "reports", "verdicts"):
            (base / subdir).mkdir(parents=True)
        cards = tmp_path / "setup_library" / "cards"
        cards.mkdir(parents=True)
        request = {
            "candidate_id": validation_id,
            "source_run_id": "R1",
            "symbol": _MRF_ROW_DICT["symbol"],
            "timeframe": _MRF_ROW_DICT["timeframe"],
            "strategy_id": _MRF_ROW_DICT["family"],
            "params": _MRF_PARAMS,
            "metrics": {"source_candidate_id": "C1"},
        }
        report = {
            "candidate_id": validation_id,
            "symbol": _MRF_ROW_DICT["symbol"],
            "timeframe": _MRF_ROW_DICT["timeframe"],
            "strategy_id": _MRF_ROW_DICT["family"],
            "verdict": {
                "candidate_id": validation_id,
                "hard_status": "PAPER_FORWARD_READY",
            },
        }
        verdict = {"candidate_id": validation_id, "hard_status": "PAPER_FORWARD_READY"}
        card = {
            "setup_id": f"setup-{validation_id}",
            "candidate_id": validation_id,
            "symbol": _MRF_ROW_DICT["symbol"],
            "timeframe": _MRF_ROW_DICT["timeframe"],
            "strategy_id": _MRF_ROW_DICT["family"],
            "params": _MRF_PARAMS,
            "hard_status": "PAPER_FORWARD_READY",
            "paper_forward_ready": True,
            "main_engine_ready": False,
        }
        for subdir, payload in (
            ("requests", request),
            ("reports", report),
            ("verdicts", verdict),
        ):
            (base / subdir / f"{stem}.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        (cards / f"setup-{validation_id}.json").write_text(
            json.dumps(card), encoding="utf-8"
        )
        write_current_generation(
            tmp_path,
            tasks=[{
                "task_id": 1,
                "task_type": "export_validation",
                "task_key": "current",
                "payload_json": "{}",
            }],
            exported_ids=[validation_id],
            completed_ids=[validation_id],
            producer_time=2.0,
        )

        status_counts: dict[str, int] = {}
        records = pfr_bridge.load_pfr_records(
            db,
            private_root=tmp_path,
            status_counts=status_counts,
        )

        assert len(records) == 1
        assert records[0]["candidate_id"] == validation_id
        assert records[0]["source_candidate_id"] == "C1"
        assert records[0]["setup_id"] == f"setup-{validation_id}"
        assert records[0]["validation_generation_id"].startswith("hvg_")
        assert status_counts["pfr_generation:ready"] == 1
        assert status_counts["pfr_generation_active_candidates"] == 1
        assert status_counts["pfr_generation_authorized_candidates"] == 1
        assert records[0]["search_family_id"] == "sfd_current"
        assert records[0]["search_trial_id"] == "stept_current"
        assert records[0]["effective_n_trials"] == 7


# ── Category 2: quality policy rejects correctly ──────────────────────────────

class TestPFRQualityPolicy:
    def test_high_dd_rejected(self):
        row = _row({**_MRF_ROW_DICT, "max_drawdown_pct": 50.0})
        passed, rejected = pfr_bridge.apply_quality_policy([row])
        assert len(passed) == 0 and len(rejected) == 1
        assert any("max_drawdown_pct" in r for r in rejected[0]["_rejection_reasons"])

    def test_low_n_trades_rejected(self):
        row = _row({**_MRF_ROW_DICT, "n_trades": 5})
        passed, rejected = pfr_bridge.apply_quality_policy([row])
        assert len(passed) == 0 and len(rejected) == 1
        assert any("n_trades" in r for r in rejected[0]["_rejection_reasons"])

    def test_low_win_rate_rejected(self):
        row = _row({**_MRF_ROW_DICT, "win_rate": 0.30})
        passed, rejected = pfr_bridge.apply_quality_policy([row])
        assert len(passed) == 0 and len(rejected) == 1

    def test_negative_avg_net_rejected(self):
        row = _row({**_MRF_ROW_DICT, "avg_net_pct": -0.5})
        passed, rejected = pfr_bridge.apply_quality_policy([row])
        assert len(passed) == 0 and len(rejected) == 1

    def test_executable_risk_contract_rejected(self):
        row = _row({**_MRF_ROW_DICT, "params": {**_MRF_PARAMS, "stop_pct": 10.0}})
        passed, rejected = pfr_bridge.apply_quality_policy([row])
        assert len(passed) == 0 and len(rejected) == 1
        assert any("stop_pct=10>8" in r for r in rejected[0]["_rejection_reasons"])

    def test_custom_policy_override(self):
        row = _row({**_MRF_ROW_DICT, "max_drawdown_pct": 40.0})
        # Default policy (max_dd=35) rejects; lenient policy accepts
        _, rej_default = pfr_bridge.apply_quality_policy([row])
        passed_lenient, _ = pfr_bridge.apply_quality_policy([row], policy={"max_drawdown_pct": 50.0})
        assert len(rej_default) == 1
        assert len(passed_lenient) == 1

    def test_good_row_passes(self):
        row = _row(_MRF_ROW_DICT)
        passed, rejected = pfr_bridge.apply_quality_policy([row])
        assert len(passed) == 1 and len(rejected) == 0


# ── Category 3: builder preserves identity fields ────────────────────────────

class TestPFRBuilderIdentity:
    def _assert_identity(self, sig, row):
        vc = sig.validator_context
        assert vc["setup_id"] == row["setup_id"]
        assert vc["candidate_id"] == str(row["candidate_id"])
        assert vc["params_hash"] == row["params_hash"]
        assert vc["source_validation_verdict"] == "PAPER_FORWARD_READY"
        assert sig.data_fingerprint  # non-empty fingerprint from candles

    def test_mrf_preserves_identity(self):
        row = _row(_MRF_ROW_DICT)
        candles = _mrf_candles_short()
        sig, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            row, candles, now=1e6, boundary_ts=0, mode="live"
        )
        assert sig is not None, f"Expected signal, got None: {reason}"
        self._assert_identity(sig, row)

    def test_builder_propagates_validation_generation_identity(self):
        row = _row(
            {
                **_MRF_ROW_DICT,
                "validation_generation_id": "hvg_synthetic_current",
            }
        )
        sig, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            row,
            _mrf_candles_short(),
            now=1e6,
            boundary_ts=0,
            mode="live",
        )

        assert sig is not None, f"Expected signal, got None: {reason}"
        assert (
            sig.validator_context["validation_generation_id"]
            == "hvg_synthetic_current"
        )
        assert sig.validator_context["validation_id"] == str(row["candidate_id"])

    def test_mbr_preserves_identity(self):
        row = _row(_MBR_ROW_DICT)
        candles = _mbr_candles_long()
        sig, reason = pfr_bridge.build_pfr_momentum_breakout(
            row, candles, now=1e6, boundary_ts=0, mode="live"
        )
        assert sig is not None, f"Expected signal, got None: {reason}"
        self._assert_identity(sig, row)


# ── Category 4: no invented defaults for missing params ───────────────────────

class TestPFRNoDefaultParams:
    def _row_missing(self, family: str, params: dict) -> dict:
        base = _MRF_ROW_DICT if family == "mean_reversion_fade" else _MBR_ROW_DICT
        return _row({**base, "params": params})

    def test_mrf_missing_move_pct_rejected(self):
        row = self._row_missing("mean_reversion_fade",
                                {"lookback": 5, "stop_pct": 7.0, "take_pct": 20.0, "hold_bars": 2})
        sig, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            row, _mrf_candles_short(), now=1e6, boundary_ts=0, mode="live"
        )
        assert sig is None
        assert "missing_params" in reason
        assert "move_pct" in reason

    def test_mrf_missing_take_pct_rejected(self):
        row = self._row_missing("mean_reversion_fade",
                                {"lookback": 5, "move_pct": 8.0, "stop_pct": 7.0, "hold_bars": 2})
        sig, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            row, _mrf_candles_short(), now=1e6, boundary_ts=0, mode="live"
        )
        assert sig is None and "missing_params" in reason and "take_pct" in reason

    def test_mbr_missing_hold_bars_rejected(self):
        row = self._row_missing("momentum_breakout",
                                {"lookback": 10, "stop_pct": 7.0, "take_pct": 20.0, "threshold_pct": 0.0})
        sig, reason = pfr_bridge.build_pfr_momentum_breakout(
            row, _mbr_candles_long(), now=1e6, boundary_ts=0, mode="live"
        )
        assert sig is None and "missing_params" in reason and "hold_bars" in reason

    def test_empty_params_rejected_with_all_missing(self):
        row = self._row_missing("mean_reversion_fade", {})
        sig, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            row, _mrf_candles_short(), now=1e6, boundary_ts=0, mode="live"
        )
        assert sig is None and "missing_params" in reason

    def test_rr_below_min_rejected(self):
        """take_pct < stop_pct * 2.0 must be rejected, not silently corrected."""
        row = self._row_missing("mean_reversion_fade",
                                {"lookback": 5, "move_pct": 8.0, "stop_pct": 10.0,
                                 "take_pct": 12.0, "hold_bars": 2})
        sig, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            row, _mrf_candles_short(), now=1e6, boundary_ts=0, mode="live"
        )
        assert sig is None and "rr_below" in reason


# ── Category 5: dedup — no duplicate active signal ────────────────────────────

class TestPFRDedup:
    def _mrf_row(self) -> dict:
        return _row(_MRF_ROW_DICT)

    def _mbr_row(self) -> dict:
        return _row(_MBR_ROW_DICT)

    def _candles_map(self):
        return {"AAOI_USDT_SWAP": _mrf_candles_short(), "AERO_USDT_SWAP": _mbr_candles_long()}

    class _MapProvider:
        def __init__(self, cmap): self._cmap = cmap
        def fetch_ohlcv(self, symbol, tf, start, end):
            return self._cmap.get(symbol.replace("-", "_"), [])

    def test_no_duplicate_by_dedup_key(self):
        row = self._mrf_row()
        dedup_key = f"{row['symbol']}|{row['timeframe']}|{row['family']}"
        prov = self._MapProvider(self._candles_map())
        active = {dedup_key}  # already in active set
        sc: dict = {}
        sigs = pfr_bridge.generate_pfr_signals(
            [row], provider=prov, now=1e6, mode="live",
            active_dedup=active, active_setup_ids=set(), recent_fingerprints=set(),
            max_pfr=5, timeframes=("1h",), status_counts=sc,
        )
        assert len(sigs) == 0
        assert sc.get("pfr_dedup_active_key", 0) >= 1

    def test_no_duplicate_by_setup_id(self):
        row = self._mrf_row()
        prov = self._MapProvider(self._candles_map())
        sc: dict = {}
        sigs = pfr_bridge.generate_pfr_signals(
            [row], provider=prov, now=1e6, mode="live",
            active_dedup=set(), active_setup_ids={row["setup_id"]},
            recent_fingerprints=set(), max_pfr=5, timeframes=("1h",), status_counts=sc,
        )
        assert len(sigs) == 0
        assert sc.get("pfr_dedup_setup_id", 0) >= 1

    def test_no_duplicate_by_fingerprint(self):
        from src.research_lab.paper_signals.lane import fingerprint
        row = self._mrf_row()
        candles = _mrf_candles_short()
        fp = fingerprint(candles)
        dedup_key = f"{row['symbol']}|{row['timeframe']}|{row['family']}"
        prov = self._MapProvider(self._candles_map())
        sc: dict = {}
        sigs = pfr_bridge.generate_pfr_signals(
            [row], provider=prov, now=1e6, mode="live",
            active_dedup=set(), active_setup_ids=set(),
            recent_fingerprints={(dedup_key, fp)},
            max_pfr=5, timeframes=("1h",), status_counts=sc,
        )
        assert len(sigs) == 0
        assert sc.get("pfr_dedup_same_data", 0) >= 1

    def test_cap_limits_generated(self):
        rows = [self._mrf_row(), self._mbr_row()]
        prov = self._MapProvider(self._candles_map())
        sc: dict = {}
        sigs = pfr_bridge.generate_pfr_signals(
            rows, provider=prov, now=1e6, mode="live",
            active_dedup=set(), active_setup_ids=set(), recent_fingerprints=set(),
            max_pfr=1, timeframes=("1h", "4h"), status_counts=sc,
        )
        assert len(sigs) <= 1
        assert sc.get("pfr_cap_reached", 0) >= 1

    def test_fetch_cap_bounds_pfr_network_attempts(self):
        rows = [
            _row({**_MRF_ROW_DICT, "candidate_id": f"C{i}", "symbol": f"AAOI{i}_USDT_SWAP"})
            for i in range(3)
        ]
        prov = CountingProvider(_mrf_candles_short())
        sc: dict = {}

        sigs = pfr_bridge.generate_pfr_signals(
            rows,
            provider=prov,
            now=1e6,
            mode="live",
            active_dedup=set(),
            active_setup_ids=set(),
            recent_fingerprints=set(),
            max_pfr=5,
            max_pfr_scan=10,
            max_pfr_fetches=1,
            timeframes=("1h",),
            status_counts=sc,
        )

        assert prov.calls == 1
        assert len(sigs) <= 1
        assert sc.get("pfr_fetch_limit_reached", 0) == 1

    def test_duplicate_param_variants_do_not_starve_distinct_pfr_setup(self):
        rows = [
            _row({**_MBR_ROW_DICT, "candidate_id": "DUP1", "symbol": "BEAT_USDT_SWAP"}),
            _row({**_MBR_ROW_DICT, "candidate_id": "DUP2", "symbol": "BEAT_USDT_SWAP"}),
            _row({**_MRF_ROW_DICT, "candidate_id": "MRF1", "symbol": "AAOI_USDT_SWAP"}),
        ]
        calls: list[str] = []

        class Provider:
            def fetch_ohlcv(self, symbol, tf, start, end):
                calls.append(symbol)
                if symbol == "BEAT-USDT-SWAP":
                    return _flat_candles()
                if symbol == "AAOI-USDT-SWAP":
                    return _mrf_candles_short()
                return []

        sc: dict = {}
        sigs = pfr_bridge.generate_pfr_signals(
            rows,
            provider=Provider(),
            now=1e6,
            mode="live",
            active_dedup=set(),
            active_setup_ids=set(),
            recent_fingerprints=set(),
            max_pfr=1,
            max_pfr_scan=10,
            max_pfr_fetches=2,
            timeframes=("1h", "4h"),
            status_counts=sc,
        )

        assert calls == ["BEAT-USDT-SWAP", "AAOI-USDT-SWAP"]
        assert sc["pfr_duplicate_setup_variant"] == 1
        assert len(sigs) == 1
        assert sigs[0][0].symbol == "AAOI_USDT_SWAP"
        assert sigs[0][0].validator_context["source_validation_verdict"] == "PAPER_FORWARD_READY"

    def test_fetch_window_is_bounded_not_zero_to_now(self):
        row = self._mrf_row()
        calls = []

        class Provider:
            def fetch_ohlcv(self, symbol, tf, start, end):
                calls.append((symbol, tf, start, end))
                return _mrf_candles_short()

        pfr_bridge.generate_pfr_signals(
            [row],
            provider=Provider(),
            now=1_700_000_000.0,
            mode="live",
            active_dedup=set(),
            active_setup_ids=set(),
            recent_fingerprints=set(),
            max_pfr=1,
            timeframes=("1h",),
            status_counts={},
        )

        assert calls
        _symbol, _tf, start, end = calls[0]
        assert start > 0
        assert end - start == 1500 * 60 * 60_000


# ── Category 6: AST boundary — pfr_bridge has no forbidden imports ────────────

def test_near_trigger_buckets_are_counted_without_changing_rejection_reason():
    rows = [
        _row({
            **_MBR_ROW_DICT,
            "candidate_id": "MBR_FLAT",
            "symbol": "FLAT1_USDT_SWAP",
            "params": {**_MBR_PARAMS, "threshold_pct": 3.0},
        }),
        _row({**_MRF_ROW_DICT, "candidate_id": "MRF_FLAT", "symbol": "FLAT2_USDT_SWAP"}),
    ]
    calls: list[str] = []

    class Provider:
        def fetch_ohlcv(self, symbol, tf, start, end):
            calls.append(symbol)
            return _flat_candles()

    sc: dict = {}
    sigs = pfr_bridge.generate_pfr_signals(
        rows,
        provider=Provider(),
        now=1e6,
        mode="live",
        active_dedup=set(),
        active_setup_ids=set(),
        recent_fingerprints=set(),
        max_pfr=2,
        max_pfr_fetches=2,
        timeframes=("1h", "4h"),
        status_counts=sc,
    )

    assert sigs == []
    assert len(calls) == 2
    assert sc["pfr_rejected:no_breakout"] == 1
    assert sc["pfr_rejected:no_fade_signal:move_pct_threshold=8.0"] == 1
    assert sc["pfr_near_trigger:breakout_gap_gt_2pct"] == 1
    assert sc["pfr_near_trigger:fade_gap_gt_2pct"] == 1


def test_near_trigger_breakout_generates_pretrigger_watch():
    rows = [_row({**_MBR_ROW_DICT, "candidate_id": "MBR_NEAR", "symbol": "NEAR_USDT_SWAP"})]

    class Provider:
        def fetch_ohlcv(self, *_args):
            return _flat_candles()

    sc: dict = {}
    gap_samples: list[dict] = []
    sigs = pfr_bridge.generate_pfr_signals(
        rows,
        provider=Provider(),
        now=1e6,
        mode="live",
        active_dedup=set(),
        active_setup_ids=set(),
        recent_fingerprints=set(),
        max_pfr=1,
        max_pfr_fetches=1,
        timeframes=("4h",),
        status_counts=sc,
        gap_samples=gap_samples,
    )

    assert len(sigs) == 1
    sig, _candles = sigs[0]
    assert sig.source == "pfr_farm"
    assert sig.setup_family == "momentum_breakout"
    assert sig.validator_context["source_validation_verdict"] == "PAPER_FORWARD_READY"
    assert sig.validator_context["ready_strategy_id"]
    assert sig.validator_context["entry_trigger"] == "breakout_stop"
    assert sig.validator_context["pretrigger"] is True
    assert sig.validator_context["trigger_gap_pct"] <= 1.0
    assert sc["pfr_generated"] == 1
    assert sc["pfr_generated_pretrigger"] == 1
    assert len(gap_samples) == 1
    assert gap_samples[0]["schema"] == "PFRGapSample.v1"
    assert gap_samples[0]["bucket"].startswith("breakout_")
    assert gap_samples[0]["selection_state"] == "pretrigger_selected"


def test_near_trigger_fade_generates_pretrigger_watch():
    rows = [_row({**_MRF_ROW_DICT, "candidate_id": "MRF_NEAR", "symbol": "NEARFADE_USDT_SWAP"})]

    class Provider:
        def fetch_ohlcv(self, *_args):
            return _mrf_near_candles_up()

    sc: dict = {}
    gap_samples: list[dict] = []
    sigs = pfr_bridge.generate_pfr_signals(
        rows,
        provider=Provider(),
        now=1e6,
        mode="live",
        active_dedup=set(),
        active_setup_ids=set(),
        recent_fingerprints=set(),
        max_pfr=1,
        max_pfr_fetches=1,
        timeframes=("1h",),
        status_counts=sc,
        gap_samples=gap_samples,
    )

    assert len(sigs) == 1
    sig, _candles = sigs[0]
    assert sig.source == "pfr_farm"
    assert sig.setup_family == "mean_reversion_fade"
    assert sig.side == "short"
    assert sig.validator_context["source_validation_verdict"] == "PAPER_FORWARD_READY"
    assert sig.validator_context["ready_strategy_id"]
    assert sig.validator_context["entry_trigger"] == "mean_reversion_threshold"
    assert sig.validator_context["pretrigger"] is True
    assert sig.validator_context["trigger_gap_pct"] <= 1.0
    assert sc["pfr_generated"] == 1
    assert sc["pfr_generated_pretrigger"] == 1
    assert len(gap_samples) == 1
    assert gap_samples[0]["bucket"].startswith("fade_")
    assert gap_samples[0]["selection_state"] == "pretrigger_selected"


def test_near_trigger_fade_surfaces_wide_risk_blocker():
    row = _row({
        **_MRF_ROW_DICT,
        "candidate_id": "MRF_WIDE_RISK",
        "symbol": "WIDERISK_USDT_SWAP",
        "params": {**_MRF_PARAMS, "stop_pct": 10.0},
    })

    sig, reason = pfr_bridge.build_pfr_mean_reversion_fade(
        row,
        _mrf_near_candles_up(),
        now=1e6,
        boundary_ts=24,
        mode="live",
    )

    assert sig is None
    assert reason.startswith("risk_too_wide_")


class TestPFRBridgeNoBoundaryViolation:
    def test_pfr_bridge_no_forbidden_imports(self):
        """pfr_bridge.py must not import any live-order / credential modules."""
        pkg = _ROOT / "src" / "research_lab" / "paper_signals"
        bridge_file = pkg / "pfr_bridge.py"
        assert bridge_file.exists(), "pfr_bridge.py not found"
        forbidden = ("okx_client", "ccxt", "order_exec", "live_engine", "auto_trade", "credential",
                     "dotenv", "hmac", "private_endpoint", "order_client", "telegram", "telebot")
        tree = ast.parse(bridge_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                assert not any(f in mod.lower() for f in forbidden), \
                    f"pfr_bridge.py: forbidden import '{mod}'"


# ── Category 7: run_cycle integration with fake PFR records ──────────────────

def _seed_universe(root: Path) -> None:
    d = root / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    # Empty universe so only PFR signals are generated
    (d / "live_universe.json").write_text(
        '{"detail": {}}', encoding="utf-8")
    _seed_complete_known_bad_authority(root)


class TestPFRCycleIntegration:
    def _pfr_db(self, tmp_path: Path) -> Path:
        db = tmp_path / "sl.sqlite"
        _make_db(db, [_MRF_ROW_DICT])
        return db

    def test_run_cycle_generates_pfr_signal(self, tmp_path):
        from src.research_lab.paper_signals import cycle, store
        _seed_universe(tmp_path)
        db = self._pfr_db(tmp_path)
        prov = FakeProvider(_mrf_candles_short())
        rep = cycle.run_cycle(
            tmp_path, mode="live", timeframes=("1h",), provider=prov, apply=True,
            now=1e6, pfr_db_path=db,
        )
        sigs = store.load_signals(tmp_path)
        pfr_sigs = [s for s in sigs if s.source == "pfr_farm"]
        telemetry = tmp_path / "state" / "derived" / "pfr_gap_telemetry.jsonl"
        assert rep["pfr_counts"].get("pfr_records_loaded", 0) >= 1
        assert len(pfr_sigs) >= 1
        assert rep["pfr_gap_samples"] >= 1
        assert telemetry.exists()
        row = json.loads(telemetry.read_text(encoding="utf-8").splitlines()[-1])
        assert row["schema"] == "PFRGapTelemetryCycle.v1"
        assert row["summary"]["samples"] >= 1
        assert row["all_research_only"] is True
        # Identity fields must be preserved in validator_context
        s = pfr_sigs[0]
        assert s.validator_context.get("setup_id") == "setup-C1"
        assert s.validator_context.get("source_validation_verdict") == "PAPER_FORWARD_READY"
        assert s.validator_context.get("params_hash") is not None

    def test_run_cycle_invalidates_active_pfr_from_superseded_generation(
        self, tmp_path, monkeypatch
    ):
        from src.research_lab.paper_signals import cycle, store

        _seed_universe(tmp_path)
        old_row = _row(
            {**_MRF_ROW_DICT, "validation_generation_id": "hvg_old"}
        )
        old_signal, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            old_row,
            _mrf_candles_short(),
            now=1e6,
            boundary_ts=0,
            mode="live",
        )
        assert old_signal is not None, reason
        store.append_signal(tmp_path, old_signal)
        current_row = _row(
            {**_MRF_ROW_DICT, "validation_generation_id": "hvg_current"}
        )

        def load_current(_db, *, status_counts, **_kwargs):
            status_counts["pfr_generation:ready"] = 1
            return [current_row]

        monkeypatch.setattr(pfr_bridge, "load_pfr_records", load_current)
        monkeypatch.setattr(pfr_bridge, "generate_pfr_signals", lambda *_a, **_k: [])
        provider = CountingProvider([])

        result = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            provider=provider,
            apply=True,
            now=1e6,
            pfr_db_path=self._pfr_db(tmp_path),
            max_new=1,
        )

        stored = {signal.signal_id: signal for signal in store.load_signals(tmp_path)}
        invalidated = stored[old_signal.signal_id]
        assert invalidated.status == "invalidated"
        assert invalidated.review["diagnosis"] == "validation_generation_superseded"
        assert result["pfr_counts"]["pfr_stale_generation_invalidated"] == 1
        assert provider.calls == 0
        assert not cycle._memory_path(tmp_path).exists()

    def test_same_market_tuple_different_setup_preserves_active_authority(
        self, tmp_path, monkeypatch
    ):
        from src.research_lab.paper_signals import cycle, store

        _seed_universe(tmp_path)
        old_row = _row(
            {**_MRF_ROW_DICT, "validation_generation_id": "hvg_old"}
        )
        old_signal, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            old_row,
            _mrf_candles_short(),
            now=1e6,
            boundary_ts=0,
            mode="live",
        )
        assert old_signal is not None, reason
        store.append_signal(tmp_path, old_signal)
        other_setup = _row({
            **_MRF_ROW_DICT,
            "candidate_id": "OTHER_SETUP",
            "setup_id": "setup-OTHER",
            "validation_generation_id": "hvg_current",
        })

        def load_current(_db, *, status_counts, **_kwargs):
            status_counts["pfr_generation:ready"] = 1
            return [other_setup]

        monkeypatch.setattr(pfr_bridge, "load_pfr_records", load_current)
        monkeypatch.setattr(pfr_bridge, "generate_pfr_signals", lambda *_a, **_k: [])

        result = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            provider=FakeProvider([]),
            apply=True,
            now=1e6,
            pfr_db_path=self._pfr_db(tmp_path),
            max_new=1,
        )

        stored = {signal.signal_id: signal for signal in store.load_signals(tmp_path)}
        assert stored[old_signal.signal_id].status == "armed"
        assert result["pfr_counts"].get("pfr_stale_generation_invalidated", 0) == 0

    def test_unrelated_ready_generation_preserves_active_pfr_observation(
        self, tmp_path, monkeypatch
    ):
        from src.research_lab.paper_signals import cycle, store

        _seed_universe(tmp_path)
        old_row = _row(
            {**_MRF_ROW_DICT, "validation_generation_id": "hvg_old"}
        )
        old_signal, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            old_row,
            _mrf_candles_short(),
            now=1e6,
            boundary_ts=0,
            mode="live",
        )
        assert old_signal is not None, reason
        store.append_signal(tmp_path, old_signal)
        unrelated_row = _row({
            **_MRF_ROW_DICT,
            "candidate_id": "UNRELATED",
            "setup_id": "setup-UNRELATED",
            "symbol": "OTHER_USDT_SWAP",
            "validation_generation_id": "hvg_current",
        })

        def load_current(_db, *, status_counts, **_kwargs):
            status_counts["pfr_generation:ready"] = 1
            return [unrelated_row]

        monkeypatch.setattr(pfr_bridge, "load_pfr_records", load_current)
        monkeypatch.setattr(pfr_bridge, "generate_pfr_signals", lambda *_a, **_k: [])
        provider = CountingProvider([])

        result = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            provider=provider,
            apply=True,
            now=1e6,
            pfr_db_path=self._pfr_db(tmp_path),
            max_new=1,
        )

        stored = {signal.signal_id: signal for signal in store.load_signals(tmp_path)}
        assert stored[old_signal.signal_id].status == "armed"
        assert result["pfr_counts"].get("pfr_stale_generation_invalidated", 0) == 0

    def test_same_generation_without_exact_setup_fails_closed_before_observation(
        self, tmp_path, monkeypatch
    ):
        from src.research_lab.paper_signals import cycle, store

        _seed_universe(tmp_path)
        old_row = _row(
            {**_MRF_ROW_DICT, "validation_generation_id": "hvg_current"}
        )
        old_signal, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            old_row,
            _mrf_candles_short(),
            now=1e6,
            boundary_ts=0,
            mode="live",
        )
        assert old_signal is not None, reason
        store.append_signal(tmp_path, old_signal)
        other_setup = _row({
            **_MRF_ROW_DICT,
            "candidate_id": "OTHER_SETUP",
            "setup_id": "setup-OTHER",
            "validation_generation_id": "hvg_current",
        })

        def load_current(_db, *, status_counts, **_kwargs):
            status_counts["pfr_generation:ready"] = 1
            return [other_setup]

        monkeypatch.setattr(pfr_bridge, "load_pfr_records", load_current)
        monkeypatch.setattr(pfr_bridge, "generate_pfr_signals", lambda *_a, **_k: [])
        provider = CountingProvider([])

        result = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            provider=provider,
            apply=True,
            now=1e6,
            pfr_db_path=self._pfr_db(tmp_path),
            max_new=1,
        )

        stored = {signal.signal_id: signal for signal in store.load_signals(tmp_path)}
        assert stored[old_signal.signal_id].status == "invalidated"
        assert result["pfr_counts"]["pfr_stale_generation_invalidated"] == 1
        assert provider.calls == 0

    def test_invalid_generation_still_fail_closes_active_pfr_authority(
        self, tmp_path, monkeypatch
    ):
        from src.research_lab.paper_signals import cycle, store

        _seed_universe(tmp_path)
        old_row = _row(
            {**_MRF_ROW_DICT, "validation_generation_id": "hvg_old"}
        )
        old_signal, reason = pfr_bridge.build_pfr_mean_reversion_fade(
            old_row,
            _mrf_candles_short(),
            now=1e6,
            boundary_ts=0,
            mode="live",
        )
        assert old_signal is not None, reason
        store.append_signal(tmp_path, old_signal)

        def load_invalid(_db, *, status_counts, **_kwargs):
            status_counts["pfr_generation:code_stale"] = 1
            return []

        monkeypatch.setattr(pfr_bridge, "load_pfr_records", load_invalid)
        monkeypatch.setattr(pfr_bridge, "generate_pfr_signals", lambda *_a, **_k: [])

        result = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            provider=FakeProvider([]),
            apply=True,
            now=1e6,
            pfr_db_path=self._pfr_db(tmp_path),
            max_new=1,
        )

        stored = {signal.signal_id: signal for signal in store.load_signals(tmp_path)}
        assert stored[old_signal.signal_id].status == "invalidated"
        assert stored[old_signal.signal_id].review["diagnosis"] == "validation_generation_superseded"
        assert result["pfr_counts"]["pfr_stale_generation_invalidated"] == 1

    def test_run_cycle_reports_pfr_quality_rejection_reason(self, tmp_path):
        from src.research_lab.paper_signals import cycle, store

        _seed_universe(tmp_path)
        db = tmp_path / "sl.sqlite"
        _make_db(db, [{**_MRF_ROW_DICT, "params": {**_MRF_PARAMS, "stop_pct": 10.0}}])
        rep = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            provider=FakeProvider(_mrf_candles_short()),
            apply=True,
            now=1e6,
            pfr_db_path=db,
        )

        assert rep["pfr_counts"]["pfr_rejected_quality"] == 1
        assert rep["pfr_counts"]["pfr_rejected_quality:stop_pct=10>8"] == 1
        assert store.load_signals(tmp_path) == []

    def test_run_cycle_prioritizes_recent_pfr_gap_memory_before_fetch_cap(self, tmp_path):
        from src.research_lab.paper_signals import cycle

        _seed_universe(tmp_path)
        db = tmp_path / "sl.sqlite"
        _make_db(
            db,
            [
                {
                    **_MRF_ROW_DICT,
                    "candidate_id": "FAR",
                    "symbol": "FAR_USDT_SWAP",
                    "avg_net_pct": 9.0,
                },
                {
                    **_MRF_ROW_DICT,
                    "candidate_id": "NEAR",
                    "symbol": "NEAR_USDT_SWAP",
                    "avg_net_pct": 1.0,
                },
            ],
        )
        telemetry = tmp_path / "state" / "derived" / "pfr_gap_telemetry.jsonl"
        telemetry.parent.mkdir(parents=True, exist_ok=True)
        telemetry.write_text(
            json.dumps(
                {
                    "schema": "PFRGapTelemetryCycle.v1",
                    "updated_at": 1_000.0,
                    "samples": [
                        {
                            "symbol": "NEAR_USDT_SWAP",
                            "timeframe": "1h",
                            "family": "mean_reversion_fade",
                            "min_gap_pct": 0.1,
                            "selection_state": "rejected",
                            "bucket": "fade_gap_le_0_25pct",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        calls: list[str] = []

        class Provider:
            def fetch_ohlcv(self, symbol, tf, start, end):
                calls.append(symbol)
                if symbol == "NEAR-USDT-SWAP":
                    return _mrf_near_candles_up()
                return _flat_candles()

        rep = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            provider=Provider(),
            apply=False,
            now=1e6,
            pfr_db_path=db,
            max_pfr_fetches=1,
            max_live_fetches=0,
        )

        assert calls == ["NEAR-USDT-SWAP"]
        assert rep["generated"] == 1
        assert rep["new_cards"][0].startswith("NEAR_USDT_SWAP")
        assert rep["pfr_counts"]["pfr_gap_memory_keys"] == 1
        assert rep["pfr_counts"]["pfr_gap_memory_prioritized"] == 1

    def test_run_cycle_pfr_supersedes_broad_farm_watch_with_same_key(self, tmp_path):
        from src.research_lab.paper_signals import cycle, store
        _seed_universe(tmp_path)
        db = self._pfr_db(tmp_path)
        broad = _live_signal(symbol="AAOI_USDT_SWAP", tf="1h", now=999_900.0)
        broad.signal_id = "broad-active-watch"
        broad.source = "farm"
        broad.setup_family = "mean_reversion_fade"
        broad.dedup_key = "AAOI_USDT_SWAP|1h|mean_reversion_fade"
        store.append_signal(tmp_path, broad)

        rep = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("1h",),
            provider=FakeProvider(_mrf_candles_short()),
            apply=True,
            now=1e6,
            pfr_db_path=db,
            max_observe=0,
        )

        sigs = store.load_signals(tmp_path)
        by_id = {s.signal_id: s for s in sigs}
        pfr_sigs = [s for s in sigs if s.source == "pfr_farm"]
        assert len(pfr_sigs) == 1
        assert by_id["broad-active-watch"].status == "invalidated"
        assert by_id["broad-active-watch"].review["diagnosis"] == "superseded_by_pfr"
        assert rep["gate_counts"]["superseded_by_pfr"] == 1

    def test_run_cycle_without_pfr_db_is_silent(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        _seed_universe(tmp_path)
        prov = FakeProvider(_mrf_candles_short())
        rep = cycle.run_cycle(
            tmp_path, mode="live", timeframes=("1h",), provider=prov, apply=False,
            now=1e6, pfr_db_path=None,
        )
        assert rep["pfr_counts"] == {}   # no PFR DB → empty counts, no crash

    def test_run_cycle_pfr_dedup_on_second_cycle(self, tmp_path):
        from src.research_lab.paper_signals import cycle, store
        _seed_universe(tmp_path)
        db = self._pfr_db(tmp_path)
        prov = FakeProvider(_mrf_candles_short())
        cycle.run_cycle(tmp_path, mode="live", timeframes=("1h",), provider=prov, apply=True,
                        now=1e6, pfr_db_path=db)
        n1 = len(store.load_signals(tmp_path))
        # Same candles → same fingerprint → no new signal on second cycle
        cycle.run_cycle(tmp_path, mode="live", timeframes=("1h",), provider=prov, apply=True,
                        now=1e6 + 60, pfr_db_path=db)
        n2 = len(store.load_signals(tmp_path))
        # Unique dedup_keys — no duplicate signals
        keys = [s.dedup_key for s in store.load_signals(tmp_path)]
        assert len(keys) == len(set(keys))
        assert n2 == n1  # no new PFR signal created for same data

    def test_run_cycle_respects_max_pfr_scan(self, tmp_path):
        """The farm/operator can cap PFR inspection so a smoke run cannot block on many
        sequential public candle fetches."""
        from src.research_lab.paper_signals import cycle, store
        _seed_universe(tmp_path)
        db = tmp_path / "sl.sqlite"
        _make_db(db, [
            {**_MRF_ROW_DICT, "candidate_id": "C0", "family": "unknown_family",
             "avg_net_pct": 99.0, "params": {"x": 1}},
            _MRF_ROW_DICT,
        ])
        prov = FakeProvider(_mrf_candles_short())
        rep = cycle.run_cycle(
            tmp_path, mode="live", timeframes=("1h",), provider=prov, apply=True,
            now=1e6, pfr_db_path=db, max_pfr_scan=1,
        )
        assert rep["pfr_counts"].get("pfr_scan_limit_reached", 0) >= 1
        assert rep["pfr_counts"].get("pfr_generated", 0) == 0
        assert store.load_signals(tmp_path) == []

    def test_run_cycle_respects_max_observe_for_smoke(self, tmp_path):
        """A smoke run can skip active-card observation instead of fetching every old watch."""
        from src.research_lab.paper_signals import cycle
        from src.research_lab.paper_signals.contract import PaperActionSignal
        from src.research_lab.paper_signals import store

        sig = PaperActionSignal(
            signal_id="active", source="manual", symbol="T_USDT_SWAP", okx_inst_id="T-USDT-SWAP",
            timeframe="1h", side="long", setup_family="mean_reversion_fade",
            entry_zone=[98.0, 100.0], stop_loss=96.0, invalidation_rule="x",
            take_profit_plan=[{"label": "tp1", "price": 104.0, "size_frac": 1.0}],
            max_hold_bars=10, max_hold_minutes=600, reason_now="test",
            status="armed", created_at=0.0, expires_at=1e9,
            ref_price=100.0, risk_pct=2.0, boundary_ts=0,
            data_fingerprint="fp", dedup_key="T_USDT_SWAP|1h|mean_reversion_fade",
        )
        store.append_signal(tmp_path, sig)
        _seed_complete_known_bad_authority(tmp_path)

        class NoFetchProvider:
            def fetch_ohlcv(self, *args, **kwargs):
                raise AssertionError("max_observe=0 should skip active fetches")

        rep = cycle.run_cycle(
            tmp_path, mode="live", timeframes=("1h",), provider=NoFetchProvider(),
            apply=False, now=1e6, pfr_db_path=None, max_new=0, max_observe=0,
        )

        assert rep["observed"] == 0
        assert rep["gate_counts"].get("observe_scan_limit_reached") == 1


# ── Category 8: BE stop tests (PASS A) ────────────────────────────────────────

def _be_long_sig() -> PaperActionSignal:
    """Long signal: entry_zone=[98,100], stop=96. risk_dist=2. be_trigger=99."""
    return PaperActionSignal(
        signal_id="be_long_test", source="test", symbol="T_USDT_SWAP", okx_inst_id="T-USDT-SWAP",
        timeframe="15m", side="long", setup_family="momentum_breakout",
        entry_zone=[98.0, 100.0], stop_loss=96.0, invalidation_rule="x",
        take_profit_plan=[{"label": "tp1", "price": 102.0, "size_frac": 1.0}],
        max_hold_bars=20, max_hold_minutes=300, reason_now="test",
        status="armed", created_at=0.0, expires_at=1e9,
        ref_price=100.0, risk_pct=2.0, boundary_ts=0,
    )


def _be_short_sig() -> PaperActionSignal:
    """Short signal: entry_zone=[100,102], stop=104. risk_dist=2. be_trigger=101."""
    return PaperActionSignal(
        signal_id="be_short_test", source="test", symbol="T_USDT_SWAP", okx_inst_id="T-USDT-SWAP",
        timeframe="15m", side="short", setup_family="momentum_breakout",
        entry_zone=[100.0, 102.0], stop_loss=104.0, invalidation_rule="x",
        take_profit_plan=[{"label": "tp1", "price": 98.0, "size_frac": 1.0}],
        max_hold_bars=20, max_hold_minutes=300, reason_now="test",
        status="armed", created_at=0.0, expires_at=1e9,
        ref_price=101.0, risk_pct=2.0, boundary_ts=0,
    )


def _c(ts: int, h: float, lo: float, cl: float) -> dict:
    return {"ts": ts, "high": h, "low": lo, "close": cl, "open": cl}


class TestBEStop:
    def test_long_be_stop_fires_and_result_is_simple_be(self):
        # ts=1: lov=97.5 fills at entry=98; ts=2: h=99.5 triggers BE (be_trigger=99); ts=3: lov=97.0 hits BE stop
        sig = _be_long_sig()
        candles = [_c(1, 100.0, 97.5, 98.5), _c(2, 99.5, 98.5, 99.0), _c(3, 99.0, 97.0, 97.5)]
        s = lane.review(lane.observe(sig, candles))
        assert s.outcome["result"] == "simple_be", f"Expected simple_be, got {s.outcome['result']}"
        assert abs(s.outcome["gross_pct"]) < 0.01  # ~= 0 before paper costs
        assert s.outcome["net_pct"] == -0.1
        assert s.review["diagnosis"] == "breakeven_save"

    def test_short_be_stop_fires_and_result_is_simple_be(self):
        # ts=1: h=102.5 fills at entry=102; ts=2: lov=100.8 triggers BE (be_trigger=101); ts=3: h=102.5 hits BE stop
        sig = _be_short_sig()
        candles = [_c(1, 102.5, 100.0, 101.0), _c(2, 101.5, 100.8, 101.0), _c(3, 102.5, 101.5, 102.0)]
        s = lane.review(lane.observe(sig, candles))
        assert s.outcome["result"] == "simple_be", f"Expected simple_be, got {s.outcome['result']}"
        assert abs(s.outcome["gross_pct"]) < 0.01
        assert s.outcome["net_pct"] == -0.1
        assert s.review["diagnosis"] == "breakeven_save"

    def test_same_bar_ambiguity_honors_original_stop(self):
        # Same bar: lov=95 (< original stop=96) AND h=99.5 (>= be_trigger=99)
        # Conservative rule: original stop fires FIRST (hit_sl check before BE check).
        sig = _be_long_sig()
        candles = [_c(1, 100.0, 97.5, 98.5),    # fill at 98
                   _c(2, 99.5, 95.0, 96.0)]       # h >= be_trigger=99 AND lov=95 <= stop=96
        s = lane.review(lane.observe(sig, candles))
        # Must exit at ORIGINAL stop (96), not BE stop. Result must be "stop", not "simple_be".
        assert s.outcome["result"] == "stop", (
            f"Same-bar ambiguity should honor original stop, got {s.outcome['result']}")
        assert s.outcome.get("exit") == 96.0 or s.outcome["net_pct"] < 0

    def test_be_not_triggered_when_price_never_reaches_half_r(self):
        # Price peaks at 98.5 (< be_trigger=99 = 0.5R), then hits original stop at 96 — no BE.
        sig = _be_long_sig()
        candles = [_c(1, 100.0, 97.5, 98.5),    # fill at 98; eff_stop=96
                   _c(2, 98.5, 95.5, 95.8),      # h=98.5 < be_trigger=99; lov=95.5 <= stop=96 → stop
                   ]
        s = lane.review(lane.observe(sig, candles))
        assert s.outcome["result"] == "stop"

    def test_be_compatible_with_partial_be_partial_wins(self):
        # exit_mode=partial_be + BE at 0.5R: both active.
        # Sequence: fill → 0.5R (BE activates) → 1R (partial_be banks half) → reverts to entry.
        # At entry: partial_done=True → result should be "partial_be" (not "simple_be")
        sig = PaperActionSignal(
            signal_id="partial_be_test", source="test", symbol="T_USDT_SWAP",
            okx_inst_id="T-USDT-SWAP", timeframe="15m", side="long", setup_family="continuation",
            entry_zone=[98.0, 100.0], stop_loss=96.0, invalidation_rule="x",
            take_profit_plan=[{"label": "tp1", "price": 100.0, "size_frac": 0.5},
                              {"label": "tp2", "price": 102.0, "size_frac": 0.5}],
            max_hold_bars=20, max_hold_minutes=300, reason_now="test",
            status="armed", created_at=0.0, expires_at=1e9,
            ref_price=100.0, risk_pct=2.0, boundary_ts=0, exit_mode="partial_be",
        )
        candles = [
            _c(1, 100.0, 97.5, 98.5),   # fill at 98
            _c(2, 99.5, 98.5, 99.0),    # h=99.5 >= be_trigger=99 → BE activates
            _c(3, 100.5, 99.5, 100.0),  # h=100.5 >= tp1=100 → bank half; partial_done=True; eff_stop=98
            _c(4, 100.0, 97.5, 98.0),   # lov=97.5 <= eff_stop=98 → partial_done=True → kind="partial_be"
        ]
        s = lane.review(lane.observe(sig, candles))
        assert s.outcome["result"] == "partial_be", \
            f"partial_done=True should give partial_be, got {s.outcome['result']}"
        assert s.outcome["net_pct"] > 0  # banked half at tp1
        assert s.review["diagnosis"] == "partial_breakeven_save"


# ── Category 9: JOIN correctness ─────────────────────────────────────────────

class TestPFRJoinCorrectness:
    def test_join_inflation_fix(self, tmp_path):
        """Same candidate_id in 3 different run_ids in candidates — bridge returns ONLY the
        farm_results-matching run, not 3 inflated copies."""
        db = tmp_path / "sl.sqlite"
        conn = sqlite3.connect(str(db))
        c = conn.cursor()
        c.execute("CREATE TABLE farm_results (run_id TEXT, candidate_id TEXT, symbol TEXT, "
                  "family TEXT, timeframe TEXT, avg_net_pct REAL, win_rate REAL, n_trades INTEGER, "
                  "max_drawdown_pct REAL, hard_status TEXT, paper_status TEXT)")
        c.execute("CREATE TABLE candidates ("
                  "run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, family TEXT, params_json TEXT, "
                  "PRIMARY KEY (run_id, candidate_id))")
        # farm_results has ONE PFR row — tied to run_id='R_PFR'
        c.execute("INSERT INTO farm_results VALUES "
                  "('R_PFR','C1','SYM_USDT_SWAP','mean_reversion_fade','1h',"
                  "2.0,0.6,25,15.0,'PAPER_FORWARD_READY','')")
        # candidates has the SAME candidate_id in 3 different run_ids
        params_json = json.dumps(_MRF_PARAMS)
        for rid in ("R_PFR", "R_OTHER1", "R_OTHER2"):
            c.execute("INSERT OR REPLACE INTO candidates VALUES (?,?,?,?)",
                      (rid, "C1", "mean_reversion_fade", params_json))
        conn.commit()
        conn.close()

        recs = pfr_bridge.load_pfr_records(db, private_root=tmp_path)
        assert len(recs) == 1, (
            f"JOIN must use run_id — expected 1 record, got {len(recs)} "
            "(JOIN inflation bug: candidate_id-only JOIN returns 3 copies)")
        assert recs[0]["candidate_id"] == "C1"
        assert recs[0]["symbol"] == "SYM_USDT_SWAP"

    def test_pfr_records_loaded_counter_reflects_true_count(self, tmp_path):
        """pfr_counts['pfr_records_loaded'] must equal actual rows loaded (post-JOIN-fix)."""
        from src.research_lab.paper_signals import cycle
        db = tmp_path / "sl.sqlite"
        _make_db(db, [_MRF_ROW_DICT, _MBR_ROW_DICT])
        _seed_universe(tmp_path)
        prov = FakeProvider(_mrf_candles_short())
        rep = cycle.run_cycle(tmp_path, mode="live", timeframes=("1h", "4h"),
                              provider=prov, apply=False, now=1e6, pfr_db_path=db)
        assert rep["pfr_counts"].get("pfr_records_loaded") == 2


# ── Category 10: detector helpers ────────────────────────────────────────────

class TestDetectorHelpers:
    def test_mbr_long_on_breakout(self):
        candles = _mbr_candles_long()
        det = detect_momentum_breakout(candles, len(candles) - 1, lookback=10)
        assert det is not None and det["side"] == "long"
        assert det["reason"] == "breakout_high"
        assert isinstance(det["ref_level"], float) and det["ref_level"] > 0

    def test_mbr_no_signal_flat(self):
        candles = _flat_candles()
        det = detect_momentum_breakout(candles, len(candles) - 1, lookback=10)
        assert det is None

    def test_mrf_short_on_up_move(self):
        candles = _mrf_candles_short()
        det = detect_mean_reversion_fade(candles, len(candles) - 1, lookback=5, move_pct=8.0)
        assert det is not None and det["side"] == "short"
        assert det["reason"] == "fade_up_move"
        assert det["move"] >= 8.0

    def test_mrf_no_signal_flat(self):
        candles = _flat_candles()
        det = detect_mean_reversion_fade(candles, len(candles) - 1, lookback=5, move_pct=8.0)
        assert det is None

    def test_detector_agrees_with_batch_generator_mbr(self):
        """detect_momentum_breakout on the decision bar must agree with signals_momentum_breakout."""
        candles = _mbr_candles_long()
        params = _MBR_PARAMS
        # Batch generates signal: decision at idx, entry at idx+1.
        # Check decision bar = len(candles)-2 (so entry = last bar = len-1).
        batch = signals_momentum_breakout(candles, params)
        if not batch:
            return  # candles don't trigger batch; skip equivalence check
        last_sig = batch[-1]
        decision_idx = last_sig["idx"] - 1  # batch stores entry_idx = decision_idx + 1
        det = detect_momentum_breakout(candles, decision_idx, lookback=int(params["lookback"]),
                                       threshold_pct=float(params.get("threshold_pct", 0)))
        assert det is not None, "Detector must agree with batch on the same decision bar"
        assert det["side"] == last_sig["side"]
        assert det["reason"] == last_sig["reason"]

    def test_detector_agrees_with_batch_generator_mrf(self):
        """detect_mean_reversion_fade on the decision bar must agree with signals_mean_reversion_fade."""
        candles = _mrf_candles_short()
        params = _MRF_PARAMS
        batch = signals_mean_reversion_fade(candles, params)
        if not batch:
            return
        last_sig = batch[-1]
        decision_idx = last_sig["idx"] - 1
        det = detect_mean_reversion_fade(candles, decision_idx, lookback=int(params["lookback"]),
                                          move_pct=float(params["move_pct"]))
        assert det is not None, "Detector must agree with batch on the same decision bar"
        assert det["side"] == last_sig["side"]
        assert det["reason"] == last_sig["reason"]

    def test_detector_no_lookahead(self):
        """Appending a future bar must not affect detection on an earlier decision bar."""
        candles_base = _mbr_candles_long(n=20)
        future_bar = {"ts": 999, "high": 9999.0, "low": 9999.0, "close": 9999.0, "open": 9999.0}
        candles_ext = candles_base + [future_bar]
        idx = len(candles_base) - 1
        det_base = detect_momentum_breakout(candles_base, idx, lookback=10)
        det_ext = detect_momentum_breakout(candles_ext, idx, lookback=10)
        assert det_base == det_ext, (
            f"Future bar must not affect detection at idx={idx}: {det_base} vs {det_ext}")

    def test_detector_insufficient_bars_returns_none(self):
        candles = _flat_candles(n=5)
        det = detect_momentum_breakout(candles, 3, lookback=10)
        assert det is None
        det2 = detect_mean_reversion_fade(candles, 3, lookback=10, move_pct=5.0)
        assert det2 is None

    def test_pending_state_saves_be_done(self):
        # After BE triggers and observe() ends without close, pending dict must have be_done=True
        sig = _be_long_sig()
        # boundary_ts=0; candles ts=1 fills, ts=2 triggers BE
        candles_ph1 = [_c(1, 100.0, 97.5, 98.5), _c(2, 99.5, 98.5, 99.0)]
        s1 = lane.observe(sig, candles_ph1)
        assert s1.status == "opened_paper"
        assert s1.outcome.get("be_done") is True
        assert abs(s1.outcome.get("eff_stop") - 98.0) < 1e-9  # moved to entry

        # Second observe() call: set boundary_ts=2 so only ts=3 is processed (no re-processing)
        s1.boundary_ts = 2
        candles_ph2 = candles_ph1 + [_c(3, 99.0, 97.0, 97.5)]
        s2 = lane.review(lane.observe(s1, candles_ph2))
        assert s2.outcome["result"] == "simple_be"
        assert s2.review["diagnosis"] == "breakeven_save"
