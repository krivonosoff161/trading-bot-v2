import ast
import json
from pathlib import Path

from src.research_lab.main_paper_runtime import observe_main_paper_runtime


class FakeProvider:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, start_ts, end_ts):
        self.calls.append((symbol, timeframe, start_ts, end_ts))
        return [c for c in self.candles if start_ts <= c["ts"] <= end_ts]


def _queue_item(runtime_id: str = "runtime_1", *, execution_allowed: bool = False, source: str = "farm") -> dict:
    return {
        "schema": "MainPaperRuntimeQueueItem.v1",
        "runtime_id": runtime_id,
        "consumer_id": "consumer_1",
        "instruction_id": "instruction_1",
        "source_signal_id": f"sig_{runtime_id}",
        "source": source,
        "pair": "BTC-USDT-SWAP",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "15m",
        "side": "long",
        "setup_family": "early_tp_tactical",
        "entry": 100.0,
        "entry_zone": [100.0, 101.0],
        "stop": 95.0,
        "take_profit_plan": [
            {"label": "tp1", "price": 106.0, "size_frac": 0.5},
            {"label": "tp2", "price": 112.0, "size_frac": 0.5},
        ],
        "max_hold_min": 150,
        "max_hold_bars": 10,
        "boundary_ts": 1_000_000,
        "created_at": 1_000.0,
        "expires_at": 10_000.0,
        "risk_pct": 5.0,
        "data_fingerprint": "fp",
        "dedup_key": "BTC|15m|early_tp_tactical",
        "source_mode": "live",
        "exit_mode": "partial_be",
        "priority": 0,
        "priority_reasons": ["test"],
        "adaptive_policy_id": "main_policy_test",
        "adaptive_execution_profile": "fast_tactical_watch",
        "adaptive_entry_profile": "limit_or_pullback",
        "adaptive_exit_profile": "early_tp_partial_be",
        "adaptive_stop_profile": "tight_atr_cap",
        "adaptive_max_hold_profile": "short",
        "adaptive_regime_hint": "impulse_exhaustion_scalp",
        "adaptive_policy_confidence": 0.7,
        "adaptive_policy_reasons": ["test_policy"],
        "runtime_action": "watch_paper",
        "source_consumer_status": "accepted_for_paper_watch",
        "paper_only": True,
        "execution_allowed": execution_allowed,
    }


def _write_queue(tmp_path: Path, items: list[dict]) -> None:
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    (derived / "main_paper_runtime_queue.json").write_text(
        json.dumps({"schema": "main_paper_runtime_adapter.v1", "items": items}),
        encoding="utf-8",
    )


def test_main_paper_runtime_observes_and_reviews_terminal_signal(tmp_path):
    _write_queue(tmp_path, [_queue_item()])
    candles = [
        {"ts": 1_000_000, "open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5, "vol": 1},
        {"ts": 1_900_000, "open": 100.0, "high": 101.5, "low": 99.0, "close": 101.0, "vol": 1},
        {"ts": 2_800_000, "open": 101.0, "high": 113.0, "low": 100.0, "close": 112.0, "vol": 1},
    ]

    summary = observe_main_paper_runtime(
        tmp_path,
        apply=True,
        provider=FakeProvider(candles),
        now_ms=3_000_000,
    )

    assert summary["rows_read"] == 1
    assert summary["observed"] == 1
    assert summary["reviewed"] == 1
    assert summary["invalid"] == 0
    assert summary["execution_allowed"] is False
    item = summary["items"][0]
    assert item["source"] == "farm"
    assert item["adaptive_policy_id"] == "main_policy_test"
    assert item["adaptive_execution_profile"] == "fast_tactical_watch"
    assert item["signal_status"] == "reviewed"
    assert item["outcome"]["result"] == "take"
    assert item["review"]["diagnosis"] == "good_signal"
    assert (tmp_path / "state" / "derived" / "main_paper_runtime_observation.json").exists()


def test_main_paper_runtime_honors_limit(tmp_path):
    _write_queue(tmp_path, [_queue_item("a"), _queue_item("b")])
    candles = [{"ts": 1_900_000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "vol": 1}]
    provider = FakeProvider(candles)

    summary = observe_main_paper_runtime(tmp_path, limit=1, provider=provider, now_ms=2_000_000)

    assert summary["rows_read"] == 1
    assert len(provider.calls) == 1


def test_main_paper_runtime_rejects_execution_enabled_queue_item(tmp_path):
    _write_queue(tmp_path, [_queue_item(execution_allowed=True)])

    summary = observe_main_paper_runtime(tmp_path, provider=FakeProvider([]), now_ms=2_000_000)

    assert summary["invalid"] == 1
    assert summary["items"][0]["status"] == "invalid"
    assert summary["execution_allowed"] is False


def test_main_paper_runtime_preserves_pfr_source(tmp_path):
    _write_queue(tmp_path, [_queue_item(source="pfr_farm")])
    candles = [
        {"ts": 1_900_000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "vol": 1},
    ]

    summary = observe_main_paper_runtime(
        tmp_path,
        provider=FakeProvider(candles),
        now_ms=2_000_000,
    )

    assert summary["invalid"] == 0
    assert summary["items"][0]["source"] == "pfr_farm"


def test_main_paper_runtime_has_no_live_order_imports():
    path = Path("src/research_lab/main_paper_runtime.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "main",
        "src.exchange",
        "src.exchange.okx_client",
        "src.utils.telegram",
        "dotenv",
        "ccxt",
        "hmac",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & forbidden)
