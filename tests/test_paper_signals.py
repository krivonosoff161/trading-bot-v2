# -*- coding: utf-8 -*-
"""Operational paper-watch lane: contract gate, store, selection gates, geometry, lifecycle, review,
and the hard no-live-order boundary. All research-only."""
import ast
import json
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


class TestReviewChart:
    def test_review_chart_writes_candlestick_png(self, tmp_path):
        sig = _good_long()
        sig.review = {"diagnosis": "good_signal"}
        candles = [
            {
                "ts": i,
                "open": 100.0 + i * 0.1,
                "high": 101.0 + i * 0.1,
                "low": 99.0 + i * 0.1,
                "close": 100.4 + i * 0.1 if i % 2 == 0 else 99.8 + i * 0.1,
                "vol": 1000,
            }
            for i in range(50)
        ]
        path = tmp_path / "chart.png"

        lane._write_chart_png(path, sig, candles)

        assert path.exists()
        assert path.read_bytes().startswith(b"\x89PNG")
        assert path.stat().st_size > 1000


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
    def test_round_preserves_micro_price_levels(self):
        # HMSTR-like instruments trade below 0.001; rounding them to 5 decimals makes
        # one display step several percent wide and distorts paper TP/SL geometry.
        assert lane._round(0.00030741, 0.00030741) == 0.00030741
        assert lane._round(100.12345, 100.0) == 100.12

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

    def test_breakout_stop_long_does_not_fill_on_pullback(self):
        s = self._sig()
        s.entry_zone = [101.0, 101.2]
        s.stop_loss = 98.0
        s.take_profit_plan = [{"label": "tp1", "price": 105.0, "size_frac": 1.0}]
        s.validator_context = {"entry_trigger": "breakout_stop"}
        candles = [
            {"ts": 900_000, "open": 100.0, "high": 100.8, "low": 99.0, "close": 100.5},
            {"ts": 1_800_000, "open": 100.5, "high": 100.9, "low": 99.4, "close": 100.7},
        ]
        out = lane.observe(s, candles)
        assert out.status == "armed"
        assert out.outcome["result"] == "pending_arm"

    def test_breakout_stop_long_fills_only_after_trigger_cross(self):
        s = self._sig()
        s.entry_zone = [101.0, 101.2]
        s.stop_loss = 98.0
        s.take_profit_plan = [{"label": "tp1", "price": 105.0, "size_frac": 1.0}]
        s.validator_context = {"entry_trigger": "breakout_stop"}
        candles = [
            {"ts": 900_000, "open": 100.0, "high": 100.8, "low": 99.0, "close": 100.5},
            {"ts": 1_800_000, "open": 100.5, "high": 101.3, "low": 100.4, "close": 101.1},
        ]
        out = lane.observe(s, candles)
        assert out.status == "opened_paper"
        assert out.outcome["entry"] == 101.2

    def test_multi_cycle_observation_does_not_replay_pre_entry_bars(self):
        phase_one = _series([100, 99, 101])
        signal = lane.observe(self._sig(), phase_one)

        assert signal.status == "opened_paper"
        assert signal.outcome["bars_held"] == 1
        assert signal.outcome["lifecycle_schema"] == "PaperSignalLifecycle.v2"
        assert signal.outcome["last_observed_bar_ts"] == phase_one[-1]["ts"]

        phase_two = list(phase_one)
        phase_two.append({"ts": 3 * 900_000, "open": 101, "high": 101.1, "low": 98.5, "close": 99})
        signal = lane.observe(signal, phase_two)

        assert signal.status == "closed_paper"
        assert signal.outcome["result"] == "simple_be"
        assert signal.outcome["bars_held"] == 2

    def test_reobserving_same_candles_is_idempotent(self):
        candles = _series([100, 99, 101])
        signal = lane.observe(self._sig(), candles)
        before = dict(signal.outcome)

        signal = lane.observe(signal, candles)

        assert signal.status == "opened_paper"
        assert signal.outcome["bars_held"] == before["bars_held"]
        assert signal.outcome["last_observed_bar_ts"] == before["last_observed_bar_ts"]
        assert signal.outcome["fresh_bars_this_cycle"] == 0

    def test_legacy_open_index_state_migrates_to_timestamp_cursor(self):
        signal = self._sig()
        signal.status = "opened_paper"
        signal.outcome = {
            "result": "pending_open",
            "entry": 99.0,
            "open_index": 1,
            "new_bars": 3,
            "eff_stop": 97.0,
        }
        candles = _series([100, 99, 100, 101, 102])

        signal = lane.observe(signal, candles)

        assert signal.status == "opened_paper"
        assert signal.outcome["bars_held"] == 2
        assert signal.outcome["last_observed_bar_ts"] == candles[-1]["ts"]
        assert "open_index" not in signal.outcome

    def test_one_shot_and_incremental_replay_match(self):
        candles = _series([100, 99, 101, 102, 103.5, 104])
        one_shot = lane.observe(PaperActionSignal.from_dict(self._sig().to_dict()), candles)
        incremental = PaperActionSignal.from_dict(self._sig().to_dict())
        for candle in candles:
            incremental = lane.observe(incremental, [candle])

        keys = ("result", "entry", "exit", "bars_waited", "bars_held", "mfe_pct", "mae_pct", "net_pct")
        assert one_shot.status == incremental.status
        assert {key: one_shot.outcome.get(key) for key in keys} == {
            key: incremental.outcome.get(key) for key in keys
        }


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
        n = len(self._p)  # anchor the last bar at end_ts so the data reads as FRESH
        return [{"ts": int(end_ts) - (n - 1 - i) * base, "open": p, "high": p * 1.01,
                 "low": p * 0.99, "close": p} for i, p in enumerate(self._p)]


class _CountingEmptyProvider:
    def __init__(self):
        self.calls = 0

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        self.calls += 1
        return []


class _RecordingEmptyProvider:
    def __init__(self):
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        self.calls.append((symbol, timeframe, start_ts, end_ts))
        return []


def _seed_universe(root: Path):
    d = root / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    (d / "live_universe.json").write_text(
        '{"detail": {"g": [{"inst_id": "AAA-USDT-SWAP", "symbol": "AAA_USDT_SWAP", '
        '"score": 9, "spread_bps": 2, "vol_usd": 50000000, "move_pct": 10}]}}', encoding="utf-8")


def _seed_large_universe(root: Path, n: int = 12):
    d = root / "discovery"
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "inst_id": f"AAA{i}-USDT-SWAP",
            "symbol": f"AAA{i}_USDT_SWAP",
            "score": 100 - i,
            "spread_bps": 2,
            "vol_usd": 50_000_000,
            "move_pct": 10,
        }
        for i in range(n)
    ]
    (d / "live_universe.json").write_text(
        json.dumps({"detail": {"g": rows}}, ensure_ascii=False),
        encoding="utf-8",
    )


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

    def test_live_fetch_cap_bounds_network_attempts(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        _seed_large_universe(tmp_path)
        prov = _CountingEmptyProvider()

        rep = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("15m", "1h", "4h"),
            provider=prov,
            apply=True,
            now=1e6,
            max_live_fetches=3,
            max_network_fetches=3,
        )

        assert prov.calls == 3
        assert rep["live_fetches"] == 3
        assert rep["max_live_fetches"] == 3
        assert rep["network_fetches"] == 3
        assert rep["max_network_fetches"] == 3
        assert rep["gate_counts"]["live_fetch_limit_reached"] == 1

    def test_stop_callback_preempts_before_network_work(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        _seed_large_universe(tmp_path)
        prov = _CountingEmptyProvider()

        rep = cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("15m", "1h", "4h"),
            provider=prov,
            apply=False,
            now=1e6,
            max_wall_seconds=45,
            should_stop=lambda: True,
        )

        assert prov.calls == 0
        assert rep["yield_requested"] is True
        assert rep["elapsed_seconds"] < 1
        assert rep["gate_counts"]["wall_or_stop_limit_reached"] >= 1

    def test_fetch_window_is_bounded_for_live_candidates(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        _seed_universe(tmp_path)
        prov = _RecordingEmptyProvider()
        now = 10_000_000.0

        cycle.run_cycle(
            tmp_path,
            mode="live",
            timeframes=("15m",),
            provider=prov,
            apply=False,
            now=now,
            max_live_fetches=1,
            max_network_fetches=1,
        )

        assert len(prov.calls) == 1
        _symbol, timeframe, start_ts, end_ts = prov.calls[0]
        assert timeframe == "15m"
        assert end_ts - start_ts == cycle.FETCH_WINDOW_BARS * 900_000

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

    def test_learn_known_bad_ignores_incomplete_or_non_string_identity(self):
        from src.research_lab.paper_signals import cycle

        malformed = [
            {"symbol": "Z", "timeframe": "15m", "diagnosis": "valid_loss"},
            {
                "symbol": 7,
                "timeframe": "15m",
                "family": "momentum_continuation",
                "diagnosis": "valid_loss",
            },
            {
                "symbol": "",
                "timeframe": "15m",
                "family": "momentum_continuation",
                "diagnosis": "valid_loss",
            },
        ]

        assert cycle.learn_known_bad(malformed, min_n=1) == set()


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

    def test_geometry_profile_changes_signal_identity_and_bounds(self):
        from src.research_lab.paper_signals import families
        candles = _series([100 + i * 0.05 for i in range(40)])
        base, why_base = families.build_early_tp("X", "X-USDT-SWAP", "15m", candles, mover={},
                                                now=1.0, boundary_ts=0, mode="live")
        runner, why_runner = families.build_early_tp(
            "X", "X-USDT-SWAP", "15m", candles, mover={}, now=1.0, boundary_ts=0, mode="live",
            geometry_profile="runner_probe")
        assert base is not None, why_base
        assert runner is not None, why_runner
        assert base.dedup_key == "X|15m|early_tp_tactical"
        assert runner.dedup_key == "X|15m|early_tp_tactical|runner_probe"
        assert runner.validator_context["geometry_profile_id"] == "runner_probe"
        assert runner.max_hold_bars > base.max_hold_bars
        assert runner.take_profit_plan[0]["price"] > base.take_profit_plan[0]["price"]

    def test_generate_can_emit_base_and_one_adaptive_profile(self):
        from src.research_lab.paper_signals import families
        candles = _series([100 + i * 0.05 for i in range(40)])
        out = families.generate(
            "X", "X-USDT-SWAP", "15m", candles, mover={}, now=1.0, boundary_ts=0, mode="live",
            families=["early_tp_tactical"],
            geometry_profiles={"early_tp_tactical": ["base", "faster_capture"]},
        )
        assert [sig.validator_context["geometry_profile_id"] for sig, _ in out] == ["base", "faster_capture"]

    def test_generate_interleaves_base_profiles_before_secondary_profiles(self):
        from src.research_lab.paper_signals import families
        candles = _series([100 + i * 0.05 for i in range(40)])
        out = families.generate(
            "X", "X-USDT-SWAP", "15m", candles, mover={}, now=1.0, boundary_ts=0, mode="live",
            families=["continuation", "early_tp_tactical"],
            geometry_profiles={
                "continuation": ["base", "stop_relief"],
                "early_tp_tactical": ["base", "faster_capture"],
            },
        )
        profile_ids = [sig.validator_context["geometry_profile_id"] for sig, _ in out]
        assert profile_ids[:2] == ["base", "base"]
        assert set(profile_ids[2:]) == {"stop_relief", "faster_capture"}

    def test_family_meta_is_structural_and_complete(self):
        from src.research_lab.paper_signals import families
        assert len(families.FAMILY_META) >= 6   # 5 builders + watch_only described structurally
        req = {"class", "when", "timeframes", "entry", "stop", "tp", "invalidation",
               "required_data", "failure_modes"}
        for name, meta in families.FAMILY_META.items():
            assert req <= set(meta), f"{name} missing meta keys"
        # every executable family has metadata; classes cover statistical/tactical/no_trade
        assert set(families.FAMILIES) <= set(families.FAMILY_META)
        assert {"statistical_candidate", "tactical", "no_trade"} <= {m["class"] for m in families.FAMILY_META.values()}


class TestLearning:
    def _seed_bad_memory(self, root, sym, tf, fam, n=3):
        from src.research_lab.paper_signals import cycle
        from src.research_lab.paper_signals.contract import PaperActionSignal
        for _ in range(n):
            s = PaperActionSignal(signal_id="i", source="farm", symbol=sym, okx_inst_id=f"{sym}-X",
                                  timeframe=tf, side="long", setup_family=fam, entry_zone=[1, 2],
                                  stop_loss=0.5, invalidation_rule="x",
                                  take_profit_plan=[{"label": "tp1", "price": 3}], max_hold_bars=10,
                                  max_hold_minutes=150, reason_now="x", dedup_key=f"{sym}|{tf}|{fam}")
            s.outcome = {"result": "stop", "net_pct": -1.0}
            s.review = {"diagnosis": "valid_loss", "net_r": -1.0}
            cycle.record_memory(root, s)

    def test_known_bad_not_regenerated(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        _seed_universe(tmp_path)
        self._seed_bad_memory(tmp_path, "AAA_USDT_SWAP", "15m", "continuation", n=3)
        prov = _FakeProvider([100 + i * 0.5 for i in range(80)])
        rep = cycle.run_cycle(tmp_path, mode="live", timeframes=("15m",), provider=prov, apply=True, now=1e6)
        assert rep["gate_counts"].get("learned_known_bad", 0) >= 1
        from src.research_lab.paper_signals import store
        fams = {s.setup_family for s in store.load_signals(tmp_path) if s.symbol == "AAA_USDT_SWAP"}
        assert "continuation" not in fams  # the learned-bad family was skipped

    def test_family_priority_orders_by_good_rate(self):
        from src.research_lab.paper_signals import cycle
        mem = ([{"family": "reversal_fade", "diagnosis": "good_signal"}] * 3 +
               [{"family": "continuation", "diagnosis": "valid_loss"}] * 3)
        order = cycle.family_priority(mem)
        assert order.index("reversal_fade") < order.index("continuation")

    def test_advice_gate_blocks_signal_minting(self):
        from src.research_lab.paper_signals import cycle
        assert cycle.validate_advice({"diagnosis_note": "looks late", "confidence": 0.7})[0]
        assert not cycle.validate_advice({"entry_zone": [1, 2]})[0]   # LLM cannot mint geometry
        assert not cycle.validate_advice({"side": "long"})[0]
        assert not cycle.validate_advice({"confidence": 5})[0]

    def test_geometry_profiles_use_outcome_memory_without_raw_prices(self):
        from src.research_lab.paper_signals import cycle
        mem = [{"symbol": "X", "timeframe": "15m", "family": "early_tp_tactical",
                "diagnosis": "bad_exit_gave_back", "result": "timeout"}]
        profiles = cycle.geometry_profiles_for_cell(
            mem, {}, symbol="X", timeframe="15m", family="early_tp_tactical")
        assert profiles == ["base", "faster_capture"]

    def test_geometry_profile_memory_demotes_losing_profile(self):
        from src.research_lab.paper_signals import cycle
        mem = [{"symbol": "X", "timeframe": "15m", "family": "early_tp_tactical",
                "diagnosis": "bad_exit_gave_back", "result": "timeout"}]
        product_memory = {
            "by_geometry_profile_cell": {
                "X|15m|early_tp_tactical|faster_capture": {
                    "terminal_rows": 3,
                    "win_rows": 0,
                    "loss_rows": 3,
                    "gave_back_rows": 2,
                    "paper_pnl_usdt": -1.2,
                }
            }
        }

        profiles = cycle.geometry_profiles_for_cell(
            mem, product_memory, symbol="X", timeframe="15m", family="early_tp_tactical")

        assert profiles == ["base"]

    def test_global_calibration_can_demote_a_profile(self):
        from src.research_lab.paper_signals import cycle

        product_memory = {
            "paper_generation_run_id": "run-v2",
            "account_generation_id": "account-v2",
            "generation_status": "completed",
            "current_generation_compatible": True,
            "display_only": False,
            "calibration": {
                "paper_generation_run_id": "run-v2",
                "account_generation_id": "account-v2",
                "generation_status": "completed",
                "current_generation_compatible": True,
                "display_only": False,
                "by_profile": {"runner_probe": {"verdict": "demote"}},
            }
        }

        profiles = cycle.geometry_profiles_for_cell(
            [], product_memory, symbol="X", timeframe="1h", family="continuation")

        assert profiles == ["base"]

    def test_stale_global_calibration_cannot_demote_a_profile(self):
        from src.research_lab.paper_signals import cycle

        product_memory = {
            "paper_generation_run_id": "run-v2",
            "account_generation_id": "account-v2",
            "generation_status": "completed",
            "current_generation_compatible": True,
            "display_only": False,
            "calibration": {
                "paper_generation_run_id": "old-run",
                "account_generation_id": "old-account",
                "generation_status": "completed",
                "current_generation_compatible": True,
                "display_only": False,
                "by_profile": {"runner_probe": {"verdict": "demote"}},
            },
        }

        profiles = cycle.geometry_profiles_for_cell(
            [],
            product_memory,
            symbol="X",
            timeframe="1h",
            family="continuation",
        )

        assert profiles == ["base", "runner_probe"]

    def test_product_memory_recomputes_calibration_instead_of_trusting_json(
        self, tmp_path, monkeypatch
    ):
        import json

        from src.research_lab import (
            setup_outcome_memory,
            trading_policy_calibration,
        )
        from src.research_lab.paper_signals import cycle

        current = {
            "paper_generation_run_id": "run-v2",
            "account_generation_id": "account-v2",
            "generation_status": "completed",
            "current_generation_compatible": True,
            "display_only": False,
        }
        monkeypatch.setattr(
            setup_outcome_memory,
            "summarize_product_training_memory",
            lambda _root: dict(current),
        )
        monkeypatch.setattr(
            trading_policy_calibration,
            "summarize_trading_policy_calibration",
            lambda _root: {
                **current,
                "by_profile": {"runner_probe": {"verdict": "retain_probe"}},
            },
        )
        derived = tmp_path / "state" / "derived"
        derived.mkdir(parents=True)
        (derived / "trading_policy_calibration.json").write_text(
            json.dumps(
                {
                    **current,
                    "by_profile": {"runner_probe": {"verdict": "demote"}},
                }
            ),
            encoding="utf-8",
        )

        memory = cycle.load_product_memory(tmp_path)

        assert memory["calibration"]["by_profile"]["runner_probe"]["verdict"] == (
            "retain_probe"
        )

    def test_product_memory_overrides_legacy_runner_on_losing_cell(self):
        from src.research_lab.paper_signals import cycle
        # Legacy memory alone would request runner_probe, but broader product
        # memory says this non-tactical cell is loss-dominant.  The next probe
        # should test stop relief, not hold longer into a bad cell.
        mem = [
            {"symbol": "X", "timeframe": "15m", "family": "liquidity_sweep_reclaim",
             "diagnosis": "good_signal", "result": "take"},
            {"symbol": "X", "timeframe": "15m", "family": "liquidity_sweep_reclaim",
             "diagnosis": "good_signal", "result": "take"},
        ]
        product_memory = {
            "by_cell": {
                "X|15m|liquidity_sweep_reclaim": {
                    "terminal_rows": 21,
                    "win_rows": 1,
                    "loss_rows": 14,
                    "gave_back_rows": 0,
                    "paper_pnl_usdt": -21.0,
                }
            }
        }

        profiles = cycle.geometry_profiles_for_cell(
            mem, product_memory, symbol="X", timeframe="15m", family="liquidity_sweep_reclaim")

        assert profiles == ["base", "stop_relief"]

    def test_losing_tactical_cell_does_not_widen_risk(self):
        from src.research_lab.paper_signals import cycle
        mem = [
            {"symbol": "X", "timeframe": "15m", "family": "early_tp_tactical",
             "diagnosis": "good_signal", "result": "take"},
            {"symbol": "X", "timeframe": "15m", "family": "early_tp_tactical",
             "diagnosis": "good_signal", "result": "take"},
        ]
        product_memory = {
            "by_cell": {
                "X|15m|early_tp_tactical": {
                    "terminal_rows": 10,
                    "win_rows": 1,
                    "loss_rows": 8,
                    "gave_back_rows": 0,
                    "paper_pnl_usdt": -4.0,
                }
            }
        }

        profiles = cycle.geometry_profiles_for_cell(
            mem, product_memory, symbol="X", timeframe="15m", family="early_tp_tactical")

        assert profiles == ["base"]

    def test_positive_product_memory_promotes_runner_probe(self):
        from src.research_lab.paper_signals import cycle
        product_memory = {
            "by_cell": {
                "X|1h|continuation": {
                    "terminal_rows": 12,
                    "win_rows": 7,
                    "loss_rows": 3,
                    "gave_back_rows": 0,
                    "paper_pnl_usdt": 9.0,
                }
            }
        }

        profiles = cycle.geometry_profiles_for_cell(
            [], product_memory, symbol="X", timeframe="1h", family="continuation")

        assert profiles == ["base", "runner_probe"]

    def test_thin_longer_timeframe_bootstraps_runner_probe(self):
        from src.research_lab.paper_signals import cycle

        profiles = cycle.geometry_profiles_for_cell(
            [], {}, symbol="X", timeframe="1h", family="continuation")

        assert profiles == ["base", "runner_probe"]

    def test_thin_tactical_cell_bootstraps_faster_capture(self):
        from src.research_lab.paper_signals import cycle

        profiles = cycle.geometry_profiles_for_cell(
            [], {}, symbol="X", timeframe="15m", family="early_tp_tactical")

        assert profiles == ["base", "faster_capture"]

    def test_thin_short_timeframe_statistical_cell_bootstraps_stop_relief(self):
        from src.research_lab.paper_signals import cycle

        profiles = cycle.geometry_profiles_for_cell(
            [], {}, symbol="X", timeframe="15m", family="liquidity_sweep_reclaim")

        assert profiles == ["base", "stop_relief"]


class TestPartialBreakevenExit:
    def _sig(self, exit_mode):
        from src.research_lab.paper_signals.contract import PaperActionSignal
        return PaperActionSignal(signal_id="i", source="farm", symbol="Z", okx_inst_id="Z-USDT-SWAP",
                                 timeframe="15m", side="long", setup_family="continuation",
                                 entry_zone=[99.0, 100.0], stop_loss=97.0, invalidation_rule="x",
                                 take_profit_plan=[{"label": "tp1", "price": 101.0, "size_frac": 0.5},
                                                   {"label": "tp2", "price": 103.0, "size_frac": 0.5}],
                                 max_hold_bars=10, max_hold_minutes=150, reason_now="x", risk_pct=2.0,
                                 status="armed", boundary_ts=0, exit_mode=exit_mode)

    def _candles(self):  # dip fills @99, spike hits tp1@101, reverts through breakeven@99
        return [{"ts": 1, "open": 100, "high": 100.2, "low": 98.5, "close": 99},
                {"ts": 2, "open": 99, "high": 101.5, "low": 99.0, "close": 101},
                {"ts": 3, "open": 101, "high": 101.0, "low": 98.9, "close": 99}]

    def test_partial_be_banks_half_and_exits_at_breakeven(self):
        s = lane.review(lane.observe(self._sig("partial_be"), self._candles()))
        assert s.outcome["result"] == "partial_be"
        assert s.outcome["net_pct"] > 0          # banked half at tp1, remainder out at breakeven
        assert s.review["diagnosis"] == "partial_breakeven_save"

    def test_fixed_mode_gives_back_to_full_stop_loss(self):
        # same path, fixed exit -> tp1 is decorative, reverts and would ride to the real stop (loss)
        candles = self._candles() + [{"ts": 4, "open": 99, "high": 99, "low": 96.5, "close": 97}]
        s = lane.review(lane.observe(self._sig("fixed"), candles))
        assert s.outcome["result"] == "stop" and s.outcome["net_pct"] < 0


class TestExitAbReport:
    def _closed(self, exit_mode, net):
        s = _good_long()
        s.signal_id = f"X_15m_{exit_mode}"
        s.setup_family = "continuation"
        s.dedup_key = "X|15m|continuation"
        s.data_fingerprint = "abc"
        s.boundary_ts = 123
        s.exit_mode = exit_mode
        s.status = "reviewed"
        s.outcome = {"result": "timeout", "net_pct": net}
        s.review = {"diagnosis": "bad_exit_gave_back", "net_r": net}
        return s

    def test_ab_report_matches_same_decision_window(self, tmp_path):
        from src.research_lab.paper_signals import ab_report
        store.append_signal(tmp_path, self._closed("fixed", -1.0))
        store.append_signal(tmp_path, self._closed("partial_be", 0.25))
        rep = ab_report.build_exit_mode_comparison(tmp_path)
        assert rep["matched_pairs"] == 1
        assert rep["verdict"] == "challenger_better"
        assert rep["delta_sum_net_pct"] == 1.25

    def test_ab_report_is_honest_when_no_pairs(self, tmp_path):
        from src.research_lab.paper_signals import ab_report
        store.append_signal(tmp_path, self._closed("partial_be", 0.25))
        rep = ab_report.build_exit_mode_comparison(tmp_path)
        assert rep["matched_pairs"] == 0
        assert rep["verdict"] == "insufficient_pairs"


class TestSearchRanking:
    def test_memory_reranks_universe(self):
        from src.research_lab.paper_signals import cycle
        movers = [{"symbol": "BAD_USDT_SWAP", "inst_id": "BAD-USDT-SWAP", "score": 10, "_bucket": "meme"},
                  {"symbol": "GOOD_USDT_SWAP", "inst_id": "GOOD-USDT-SWAP", "score": 9, "_bucket": "majors"}]
        memory = [{"symbol": "GOOD_USDT_SWAP", "diagnosis": "good_signal"}] * 3
        known_bad = {("BAD_USDT_SWAP", "*")}
        ranked = cycle.rank_movers(movers, memory, known_bad)
        # despite lower raw score, GOOD outranks BAD (penalty 5 vs bonus +1.5)
        assert ranked[0]["symbol"] == "GOOD_USDT_SWAP"
        assert "knownbad-5" in ranked[1]["_reason"]

    def test_product_memory_softly_reranks_universe(self):
        from src.research_lab.paper_signals import cycle
        movers = [
            {"symbol": "WEAK_USDT_SWAP", "inst_id": "WEAK-USDT-SWAP", "score": 10, "_bucket": "meme"},
            {"symbol": "BETTER_USDT_SWAP", "inst_id": "BETTER-USDT-SWAP", "score": 9, "_bucket": "majors"},
        ]
        product_memory = {
            "by_cell": {
                "WEAK_USDT_SWAP|15m|continuation": {
                    "terminal_rows": 10,
                    "win_rows": 1,
                    "loss_rows": 8,
                    "gave_back_rows": 3,
                    "paper_pnl_usdt": -4.0,
                },
                "BETTER_USDT_SWAP|15m|continuation": {
                    "terminal_rows": 10,
                    "win_rows": 7,
                    "loss_rows": 2,
                    "gave_back_rows": 0,
                    "paper_pnl_usdt": 3.0,
                },
            }
        }

        ranked = cycle.rank_movers(movers, [], set(), product_memory)

        assert ranked[0]["symbol"] == "BETTER_USDT_SWAP"
        assert "product_memory=+" in ranked[0]["_reason"]
        assert "product_memory=-" in ranked[1]["_reason"]

    def test_selection_snapshot_written(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        ranked = cycle.rank_movers([{"symbol": "A_USDT_SWAP", "score": 5, "_bucket": "majors"}], [], set())
        p = cycle.write_selection_snapshot(tmp_path, ranked)
        import json
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["total"] == 1 and d["top"][0]["symbol"] == "A_USDT_SWAP" and "majors" in d["by_bucket"]


class TestPaperLoopSafety:
    def test_run_loop_rejects_active_lock(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        lock = tmp_path / "state" / "paper_signals_loop.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text("active", encoding="utf-8")
        try:
            cycle.run_loop(tmp_path, cycles=1, lock_file=lock, provider=_FakeProvider([100] * 80))
            assert False, "active lock should abort"
        except RuntimeError as exc:
            assert "another paper_signals loop appears active" in str(exc)

    def test_run_loop_removes_lock_after_success(self, tmp_path):
        from src.research_lab.paper_signals import cycle
        _seed_universe(tmp_path)
        lock = tmp_path / "state" / "paper_signals_loop.lock"
        cycle.run_loop(tmp_path, cycles=1, lock_file=lock, provider=_FakeProvider([100 + i * 0.5 for i in range(80)]))
        assert not lock.exists()

    def test_run_loop_forwards_bounded_pfr_fetch_budget(
        self,
        tmp_path,
        monkeypatch,
    ):
        from src.research_lab.paper_signals import cycle

        observed = []

        def fake_run_cycle(_private_root, **kwargs):
            observed.append(kwargs)
            return {"cycle": len(observed)}

        monkeypatch.setattr(cycle, "run_cycle", fake_run_cycle)

        reports = cycle.run_loop(
            tmp_path,
            cycles=2,
            max_pfr_scan=7,
            max_pfr_fetches=3,
        )

        assert reports == [{"cycle": 1}, {"cycle": 2}]
        assert [row["max_pfr_scan"] for row in observed] == [7, 7]
        assert [row["max_pfr_fetches"] for row in observed] == [3, 3]
        assert not (tmp_path / "state" / "paper_signals_loop.lock").exists()


class TestAgeOut:
    def _armed(self, expires_at=100.0):
        from src.research_lab.paper_signals.contract import PaperActionSignal
        return PaperActionSignal(signal_id="i", source="farm", symbol="Z", okx_inst_id="Z-USDT-SWAP",
                                 timeframe="15m", side="long", setup_family="continuation",
                                 entry_zone=[99, 100], stop_loss=97, invalidation_rule="x",
                                 take_profit_plan=[{"label": "tp1", "price": 102}], max_hold_bars=10,
                                 max_hold_minutes=150, reason_now="x", status="armed", expires_at=expires_at)

    def test_armed_past_expiry_expires(self):
        s = self._armed(expires_at=100.0)
        assert lane.age_out(s, now=200.0) is True
        assert s.status == "expired" and s.outcome["reason"] == "stale_past_expiry"

    def test_repeated_no_data_expires(self):
        s = self._armed(expires_at=1e12)
        s.outcome = {"no_data_count": 4}
        assert lane.age_out(s, now=1.0) is True
        assert s.outcome["reason"] == "no_data_repeated"

    def test_fresh_armed_survives(self):
        assert lane.age_out(self._armed(expires_at=1e12), now=1.0) is False

    def test_opened_signal_does_not_become_expired_no_entry(self):
        signal = self._armed(expires_at=100.0)
        signal.status = "opened_paper"
        signal.outcome = {"result": "pending_open", "entry": 99.0, "bars_held": 2}

        assert lane.age_out(signal, now=200.0) is False
        assert signal.status == "opened_paper"
        assert signal.outcome["entry"] == 99.0

    def test_opened_signal_with_repeated_no_data_is_invalidated(self):
        signal = self._armed(expires_at=1e12)
        signal.status = "opened_paper"
        signal.outcome = {"entry": 99.0, "no_data_count": 4}

        assert lane.age_out(signal, now=1.0) is True
        assert signal.status == "invalidated"
        assert signal.outcome["lifecycle_schema"] == "PaperSignalLifecycle.v2"
        assert signal.outcome["result"] == "no_data"
        assert signal.outcome["reason"] == "no_data_repeated_opened"


class TestKnownBadGate:
    def test_symbol_wide_and_family_block(self, tmp_path):
        import json
        d = tmp_path / "state" / "derived"
        d.mkdir(parents=True)
        (d / "setup_outcome_memory.json").write_text(json.dumps({"records": [
            {"symbol": "BAD_USDT_SWAP", "family": "reversal_fade", "outcome_class": "CONFIRMED_BAD"}]}),
            encoding="utf-8")
        kb = lane.load_known_bad(tmp_path)
        assert ("BAD_USDT_SWAP", "reversal_fade") in kb and ("BAD_USDT_SWAP", "*") in kb
        # a DIFFERENT family on the same symbol is blocked by the symbol-wide entry
        g = lane.gate_candidate({"inst_id": "BAD-USDT-SWAP", "symbol": "BAD_USDT_SWAP", "spread_bps": 1,
                                 "vol_usd": 9e7, "_tf": "15m"},
                                _series([1.0] * 60), now_ms=0, known_bad=kb, family="continuation")
        assert g == "known_bad_in_memory"


class TestRicherDiagnosis:
    def _sig(self, risk_pct=2.0):
        from src.research_lab.paper_signals.contract import PaperActionSignal
        return PaperActionSignal(signal_id="i", source="farm", symbol="Z", okx_inst_id="Z-X",
                                 timeframe="15m", side="long", setup_family="continuation",
                                 entry_zone=[99, 100], stop_loss=98, invalidation_rule="x",
                                 take_profit_plan=[{"label": "tp1", "price": 104}], max_hold_bars=10,
                                 max_hold_minutes=150, reason_now="x", risk_pct=risk_pct)

    def test_wrong_direction(self):
        s = self._sig()
        s.outcome = {"result": "stop", "mfe_pct": 0.1, "mae_pct": 2.0}
        assert lane.review(s).review["diagnosis"] == "wrong_direction"

    def test_target_too_far(self):
        s = self._sig()
        s.outcome = {"result": "timeout", "mfe_pct": 4.0, "mae_pct": 1.0}  # 2R favourable, never hit TP
        assert lane.review(s).review["diagnosis"] == "target_too_far"


class TestNoLiveBoundary:
    def test_notify_does_not_fallback_to_scanner_chat(self, monkeypatch):
        from scripts import subscriptions
        from scripts.strategy_lab import paper_signals_run

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
        monkeypatch.delenv("PAPER_CHAT_ID", raising=False)
        monkeypatch.setenv("SCANNER_CHAT_ID", "scanner-chat")
        monkeypatch.setattr(subscriptions, "list_delivery_users", lambda: [])

        assert paper_signals_run._notify(["card"]) == "skipped:no_active_subscribers"

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
