# -*- coding: utf-8 -*-
"""Operational paper-watch lane: contract gate, store, selection gates, geometry, lifecycle, review,
and the hard no-live-order boundary. All research-only."""
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.research_lab.paper_signals import lane, store  # noqa: E402
from src.research_lab.paper_signals.contract import (  # noqa: E402
    PaperActionSignal, render_card, validate_signal)


def _good_long() -> PaperActionSignal:
    return PaperActionSignal(
        signal_id="X_15m_1", source="farm", symbol="X_USDT_SWAP", okx_inst_id="X-USDT-SWAP",
        timeframe="15m", side="long", setup_family="momentum_continuation",
        entry_zone=[100.0, 101.0], stop_loss=98.0, invalidation_rule="close below 98",
        take_profit_plan=[{"label": "tp1", "price": 103.0, "size_frac": 1.0}],
        max_hold_bars=28, max_hold_minutes=420, reason_now="live mover continuation",
        status="armed", created_at=1000.0, expires_at=2000.0, ref_price=101.0, risk_pct=2.0,
        boundary_ts=0)


class TestContractGate:
    def test_good_signal_passes(self):
        ok, problems = validate_signal(_good_long())
        assert ok, problems

    def test_long_stop_must_be_below_zone(self):
        s = _good_long()
        s.stop_loss = 100.5
        assert not validate_signal(s)[0]

    def test_long_tp_must_be_above_zone(self):
        s = _good_long()
        s.take_profit_plan = [{"label": "tp1", "price": 100.5}]
        assert not validate_signal(s)[0]

    def test_bad_zone_rejected(self):
        s = _good_long()
        s.entry_zone = [101.0, 100.0]
        assert not validate_signal(s)[0]

    def test_render_card_is_paper_only(self):
        card = render_card(_good_long())
        assert "PAPER WATCH" in card and "NOT an order" in card


class TestStore:
    def test_append_load_update_latest_wins(self, tmp_path):
        s = _good_long()
        store.append_signal(tmp_path, s)
        s.status = "opened_paper"
        store.update_signal(tmp_path, s)
        loaded = store.load_signals(tmp_path)
        assert len(loaded) == 1 and loaded[0].status == "opened_paper"

    def test_invalid_is_rejected_by_store(self, tmp_path):
        s = _good_long()
        s.stop_loss = 200.0  # above zone for a long -> invalid
        try:
            store.append_signal(tmp_path, s)
            assert False, "should have raised"
        except ValueError:
            assert True


def _series(prices):
    return [{"ts": i * 900_000, "open": p, "high": p * 1.002, "low": p * 0.998, "close": p}
            for i, p in enumerate(prices)]


class TestSelectionGates:
    def test_not_tradeable(self):
        assert lane.gate_candidate({"inst_id": "X/USD"}, _series([1] * 60), now_ms=10**13,
                                   known_bad=set()) == "not_tradeable_inst"

    def test_volume_too_thin(self):
        c = _series([1.0] * 60)
        mv = {"inst_id": "X-USDT-SWAP", "spread_bps": 1, "vol_usd": 1_000_000, "_tf": "15m"}
        # make data fresh so the volume gate is the one that trips
        for i, cc in enumerate(c):
            cc["ts"] = 10**13 - (60 - i) * 900_000
        assert lane.gate_candidate(mv, c, now_ms=10**13, known_bad=set()) == "volume_too_thin"


class TestGeometry:
    def test_builds_valid_long_or_short(self):
        candles = _series([100 + i * 0.5 for i in range(60)])  # uptrend
        sig, why = lane.build_signal("X_USDT_SWAP", "X-USDT-SWAP", "15m", candles, source="farm",
                                     mover={"move_pct": 5}, now=1000.0, boundary_ts=0)
        assert why == "ok" and sig.side == "long"
        assert validate_signal(sig)[0]
        assert sig.risk_pct > 0 and sig.entry_zone[0] < sig.entry_zone[1]

    def test_risk_too_wide_rejected(self):
        # huge ATR vs price -> risk over the cap
        candles = [{"ts": i * 900_000, "open": 100, "high": 130, "low": 70, "close": 100 + i}
                   for i in range(60)]
        sig, why = lane.build_signal("X_USDT_SWAP", "X-USDT-SWAP", "15m", candles, source="farm",
                                     now=1000.0, boundary_ts=0)
        assert sig is None and why.startswith("risk_too_wide")


class TestLifecycle:
    def _sig(self):
        return PaperActionSignal(
            signal_id="X_15m_1", source="farm", symbol="X", okx_inst_id="X-USDT-SWAP", timeframe="15m",
            side="long", setup_family="momentum_continuation", entry_zone=[99.0, 100.0], stop_loss=97.0,
            invalidation_rule="x", take_profit_plan=[{"label": "tp1", "price": 103.0, "size_frac": 1.0}],
            max_hold_bars=10, max_hold_minutes=150, reason_now="x", status="armed",
            created_at=0, expires_at=10, ref_price=100.0, risk_pct=2.0, boundary_ts=0)

    def test_fill_then_take(self):
        # dip to 99 (fill), then run to 103 (take)
        candles = _series([100, 99, 100, 101, 103.5])
        s = lane.observe(self._sig(), candles)
        assert s.status == "opened_paper" or s.outcome["result"] in ("take", "pending_open")
        # extend so it clearly takes
        candles = _series([100, 99, 101, 102, 103.5, 104])
        s = lane.observe(self._sig(), candles)
        assert s.status == "closed_paper" and s.outcome["result"] == "take"

    def test_expired_no_entry(self):
        candles = _series([100, 101, 102, 103, 104, 105, 106, 107])  # never dips to 99
        s = lane.observe(self._sig(), candles)
        assert s.status == "expired" and s.outcome["result"] == "expired_no_entry"

    def test_stop(self):
        candles = _series([100, 99, 98, 96.5])  # fill at 99 then drop through 97
        s = lane.observe(self._sig(), candles)
        assert s.status == "closed_paper" and s.outcome["result"] == "stop"


class TestReview:
    def test_diagnoses(self):
        s = lane.observe(TestLifecycle()._sig(), _series([100, 99, 101, 102, 103.5, 104]))
        s = lane.review(s)
        assert s.review["diagnosis"] == "good_signal" and s.status == "reviewed"


class _FakeProvider:
    """Deterministic synthetic candles so the cycle is testable without network."""
    def __init__(self, prices):
        self._p = prices

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        base = 900_000
        return [{"ts": i * base, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p}
                for i, p in enumerate(self._p)]


def _seed_universe(root: Path):
    d = root / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    (d / "live_universe.json").write_text(
        '{"detail": {"g": [{"inst_id": "AAA-USDT-SWAP", "symbol": "AAA_USDT_SWAP", '
        '"score": 9, "spread_bps": 2, "vol_usd": 50000000, "move_pct": 10}]}}', encoding="utf-8")


class TestCycle:
    def test_dry_writes_nothing(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        _seed_universe(tmp_path)
        prov = _FakeProvider([100 + i * 0.5 for i in range(80)])
        cycle.run_cycle(tmp_path, mode="replay", timeframes=("15m",), provider=prov, apply=False, now=1e6)
        assert not (tmp_path / "state" / "derived" / "paper_signals.jsonl").exists()

    def test_apply_then_dedup_no_duplicate(self, tmp_path):
        from src.research_lab.paper_signals import cycle, store
        _seed_universe(tmp_path)
        prov = _FakeProvider([100 + i * 0.5 for i in range(80)])
        cycle.run_cycle(tmp_path, mode="live", timeframes=("15m",), provider=prov, apply=True, now=1e6)
        n1 = len(store.load_signals(tmp_path))
        cycle.run_cycle(tmp_path, mode="live", timeframes=("15m",), provider=prov, apply=True, now=1e6 + 10)
        keys = [s.dedup_key for s in store.load_signals(tmp_path)]
        assert len(keys) == len(set(keys)) and len(keys) == n1

    def test_memory_roundtrip_and_learn_known_bad(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        from src.research_lab.paper_signals.contract import PaperActionSignal
        for _ in range(3):
            s = PaperActionSignal(signal_id="i", source="farm", symbol="Z", okx_inst_id="Z-USDT-SWAP",
                                  timeframe="15m", side="long", setup_family="momentum_continuation",
                                  entry_zone=[1, 2], stop_loss=0.5, invalidation_rule="x",
                                  take_profit_plan=[{"label": "tp1", "price": 3}], max_hold_bars=10,
                                  max_hold_minutes=150, reason_now="x", dedup_key="Z|15m|momentum_continuation")
            s.outcome = {"result": "stop", "net_pct": -1.0}
            s.review = {"diagnosis": "valid_loss", "net_r": -1.0}
            cycle.record_memory(tmp_path, s)
        mem = cycle.load_memory(tmp_path)
        assert len(mem) == 3
        assert ("Z", "15m", "momentum_continuation") in cycle.learn_known_bad(mem, min_n=3)


class TestFamilies:
    def test_continuation_rejects_exhausted(self):
        from src.research_lab.paper_signals import families
        # a clean strong run -> extended -> continuation must refuse to chase
        candles = _series([100 + i * 2.0 for i in range(40)])
        sig, why = families.build_continuation("X", "X-USDT-SWAP", "15m", candles, mover={},
                                               now=1.0, boundary_ts=0, mode="live")
        assert sig is None and why == "entry_after_move_exhausted"

    def test_fade_requires_extension(self):
        from src.research_lab.paper_signals import families
        candles = _series([100 + (0.1 if i % 2 else -0.1) for i in range(40)])  # calm/choppy
        sig, why = families.build_fade("X", "X-USDT-SWAP", "15m", candles, mover={}, now=1.0,
                                       boundary_ts=0, mode="live")
        assert sig is None and why == "not_extended_no_fade"

    def test_nontactical_enforces_rr2(self):
        from src.research_lab.paper_signals import families
        candles = _series([100 + i * 0.3 for i in range(40)])  # mild uptrend, not extended
        sig, why = families.build_continuation("X", "X-USDT-SWAP", "15m", candles, mover={},
                                               now=1.0, boundary_ts=0, mode="live")
        if sig is not None:
            risk = sig.entry_zone[1] - sig.stop_loss
            assert (sig.take_profit_plan[0]["price"] - sig.entry_zone[1]) >= 2 * risk - 1e-6

    def test_at_least_three_families_available(self):
        from src.research_lab.paper_signals import families
        assert len(families.FAMILIES) >= 3
        assert {"continuation", "reversal_fade", "liquidity_sweep_reclaim"} <= set(families.FAMILIES)


class TestNoLiveBoundary:
    def test_no_forbidden_imports_in_lane(self):
        pkg = _ROOT / "src" / "research_lab" / "paper_signals"
        forbidden = ("okx_client", "ccxt", "order_exec", "live_engine", "auto_trade", "credential",
                     "dotenv", "hmac", "private_endpoint", "order_client")
        for py in list(pkg.glob("*.py")) + [_ROOT / "scripts" / "strategy_lab" / "paper_signals_run.py"]:
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    mod = (getattr(node, "module", "") or "") + " ".join(a.name for a in node.names)
                    assert not any(f in mod.lower() for f in forbidden), f"{py.name}: forbidden {mod}"
