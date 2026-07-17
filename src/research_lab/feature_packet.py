"""Code-only feature and geometry packets for bounded farm/LLM review."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now
from src.research_lab.market_data_packet import MarketDataPacket
from src.research_lab.candle_identity import candle_slice_fingerprint
from src.research_lab.trade_math import CostAssumptions, capture, geometry

SCHEMA = "DecisionFeaturePacket.v1"
OUTCOME_SCHEMA = "OutcomeFeaturePacket.v1"
_OUTCOME_ONLY_FIELDS = {
    "mfe_pct", "mae_pct", "capture_at_entry", "net_pct", "pnl_pct",
    "outcome", "r_multiple", "tp_before_sl", "bars_to_tp", "bars_to_sl",
}


def packet_dir(private_root: Path) -> Path:
    return Path(private_root) / "features" / "decision"


def outcome_packet_dir(private_root: Path) -> Path:
    return Path(private_root) / "features" / "outcome"


def packet_index_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "lineage" / "decision_feature_packets.jsonl"


def outcome_packet_index_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "lineage" / "outcome_feature_packets.jsonl"


def _float(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _atr(candles: list[dict[str, Any]], n: int = 14) -> float:
    if len(candles) < n + 1:
        return 0.0
    trs: list[float] = []
    for i in range(len(candles) - n, len(candles)):
        h = _float(candles[i], "high")
        lo = _float(candles[i], "low")
        pc = _float(candles[i - 1], "close")
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return round(sum(trs) / len(trs), 10) if trs else 0.0


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2.0 / (period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = value * k + ema * (1 - k)
    return round(ema, 10)


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 0.0
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(values[-period - 1:-1], values[-period:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 4)


def _volume_spike(candles: list[dict[str, Any]], lookback: int = 20) -> float:
    if len(candles) < 2:
        return 0.0
    vols = [_float(c, "vol") for c in candles[-lookback - 1:-1]]
    base = sum(vols) / len(vols) if vols else 0.0
    if base <= 0:
        return 0.0
    return round(_float(candles[-1], "vol") / base, 4)


def _future_metrics(packet: MarketDataPacket, side: str, entry: float) -> dict[str, Any]:
    if not packet.future_window or entry <= 0:
        return {}
    long_ = side == "long"
    highs = [_float(c, "high") for c in packet.future_window]
    lows = [_float(c, "low") for c in packet.future_window]
    mfe = ((max(highs) - entry) if long_ else (entry - min(lows))) / entry * 100.0
    mae = ((entry - min(lows)) if long_ else (max(highs) - entry)) / entry * 100.0
    return {"mfe_pct": round(max(0.0, mfe), 6), "mae_pct": round(max(0.0, mae), 6)}


@dataclass(frozen=True)
class FeaturePacket:
    feature_packet_id: str
    scanner_event_id: str
    data_packet_id: str
    symbol: str
    instrument: str
    timeframe: str
    mode: str
    features: dict[str, Any]
    geometry: dict[str, Any]
    data_quality: dict[str, Any]
    no_lookahead: bool
    created_at: str = field(default_factory=utc_now)
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        leaked = sorted(_OUTCOME_ONLY_FIELDS.intersection(payload.get("features") or {}))
        if leaked:
            raise ValueError(f"decision feature packet contains outcome-only fields: {leaked}")
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeaturePacket":
        known = {f for f in cls.__dataclass_fields__}  # noqa: F821 - dataclass attr
        return cls(**{k: v for k, v in data.items() if k in known})


def load_feature_packet(path: Path) -> FeaturePacket:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("feature packet must be a JSON object")
    if data.get("schema") != SCHEMA:
        raise ValueError(f"unsupported feature packet schema: {data.get('schema')!r}")
    packet = FeaturePacket.from_dict(data)
    packet.to_dict()  # enforce the decision/outcome boundary on persisted input
    return packet


DecisionFeaturePacket = FeaturePacket


@dataclass(frozen=True)
class OutcomeFeaturePacket:
    outcome_packet_id: str
    decision_packet_id: str
    scanner_event_id: str
    data_packet_id: str
    future_evidence_id: str
    symbol: str
    instrument: str
    timeframe: str
    mode: str
    outcome_features: dict[str, Any]
    temporal_provenance: dict[str, Any]
    label_quality: dict[str, Any]
    created_at: str = field(default_factory=utc_now)
    schema: str = OUTCOME_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def latest_feature_packet_path(private_root: Path) -> Path | None:
    index = packet_index_path(private_root)
    if not index.exists():
        return None
    latest = ""
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        latest = str(row.get("path") or latest)
    return Path(latest) if latest else None


def build_feature_packet(
    packet: MarketDataPacket,
    *,
    side: str = "",
    entry_zone: list[float] | None = None,
    stop_loss: float = 0.0,
    take_profit_plan: list[dict[str, Any]] | None = None,
) -> FeaturePacket:
    candles = packet.ohlcv_window
    closes = [_float(c, "close") for c in candles]
    last = candles[-1] if candles else {}
    price = _float(last, "close")
    atr = _atr(candles)
    lookback = min(20, max(1, len(closes) - 1))
    trend_delta = price - closes[-1 - lookback] if len(closes) > lookback else 0.0
    trend_atr = round(trend_delta / atr, 6) if atr > 0 else 0.0
    swing_lows = [_float(c, "low") for c in candles[-20:]]
    swing_highs = [_float(c, "high") for c in candles[-20:]]
    geom = geometry(entry_zone or [], stop_loss, take_profit_plan or [], side) if side else {}
    features = {
        "last_close": price,
        "atr": atr,
        "trend_delta": round(trend_delta, 10),
        "trend_atr": trend_atr,
        "regime": "up" if trend_atr > 0.5 else "down" if trend_atr < -0.5 else "range",
        "impulse": abs(trend_atr) >= 1.5,
        "late_entry": abs(trend_atr) >= 3.0,
        "swing_low": round(min(swing_lows), 10) if swing_lows else 0.0,
        "swing_high": round(max(swing_highs), 10) if swing_highs else 0.0,
        "ema_20": _ema(closes[-60:], 20),
        "rsi_14": _rsi(closes),
        "volume_spike": _volume_spike(candles),
        "fees_slippage": CostAssumptions().to_dict(),
    }
    payload = {
        "scanner_event_id": packet.scanner_event_id,
        "data_packet_id": packet.data_packet_id,
        "symbol": packet.symbol,
        "timeframe": packet.timeframe,
        "mode": packet.mode,
        "side": side,
        "geometry": geom,
    }
    return FeaturePacket(
        feature_packet_id=stable_id("fp", payload),
        scanner_event_id=packet.scanner_event_id,
        data_packet_id=packet.data_packet_id,
        symbol=packet.symbol,
        instrument=packet.instrument,
        timeframe=packet.timeframe,
        mode=packet.mode,
        features=features,
        geometry=geom,
        data_quality={
            "flags": list(packet.data_quality_flags),
            "past_bars": len(packet.ohlcv_window),
            "as_of_ts": packet.ohlcv_window[-1].get("ts") if packet.ohlcv_window else None,
            "snapshot_manifest_id": packet.snapshot_manifest_id,
            "snapshot_provenance_status": packet.snapshot_manifest.get("provenance_status"),
        },
        no_lookahead=True,
    )


def build_outcome_feature_packet(
    packet: MarketDataPacket,
    decision_packet: FeaturePacket,
    *,
    side: str = "",
) -> OutcomeFeaturePacket | None:
    """Build a physically separate post-decision packet; never used by decision roles."""
    entry = float(decision_packet.geometry.get("entry_mid") or 0.0)
    outcome = _future_metrics(packet, side, entry) if side else {}
    if not packet.future_window or not outcome:
        return None
    outcome["capture_at_entry"] = capture(0.0, float(outcome.get("mfe_pct") or 0.0))
    outcome_content_hash = candle_slice_fingerprint(
        packet.symbol, packet.timeframe, packet.future_window,
    )
    payload = {
        "decision_packet_id": decision_packet.feature_packet_id,
        "data_packet_id": packet.data_packet_id,
        "future_evidence_id": packet.future_evidence_id,
        "future_first_ts": packet.future_window[0].get("ts"),
        "future_last_ts": packet.future_window[-1].get("ts"),
        "outcome_content_hash": outcome_content_hash,
        "outcome_features": outcome,
    }
    return OutcomeFeaturePacket(
        outcome_packet_id=stable_id("ofp", payload),
        decision_packet_id=decision_packet.feature_packet_id,
        scanner_event_id=packet.scanner_event_id,
        data_packet_id=packet.data_packet_id,
        future_evidence_id=packet.future_evidence_id,
        symbol=packet.symbol,
        instrument=packet.instrument,
        timeframe=packet.timeframe,
        mode=packet.mode,
        outcome_features=outcome,
        temporal_provenance={
            "decision_last_ts": packet.ohlcv_window[-1].get("ts") if packet.ohlcv_window else None,
            "outcome_first_ts": packet.future_window[0].get("ts"),
            "outcome_last_ts": packet.future_window[-1].get("ts"),
            "outcome_content_hash": outcome_content_hash,
            "future_evidence_id": packet.future_evidence_id,
            "available_after_decision": True,
        },
        label_quality={
            "trajectory_precision": "ohlc_window_unknown_intrabar_order",
            "decision_role_eligible": False,
            "outcome_role_eligible": True,
        },
    )


def write_feature_packet(private_root: Path, packet: FeaturePacket) -> Path:
    out = packet_dir(private_root) / f"{packet.feature_packet_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_jsonl(
        packet_index_path(private_root),
        {
            "schema": "DecisionFeaturePacketIndex.v1",
            "feature_packet_id": packet.feature_packet_id,
            "data_packet_id": packet.data_packet_id,
            "scanner_event_id": packet.scanner_event_id,
            "symbol": packet.symbol,
            "instrument": packet.instrument,
            "timeframe": packet.timeframe,
            "mode": packet.mode,
            "path": str(out),
            "regime": packet.features.get("regime"),
            "data_quality_flags": packet.data_quality.get("flags") or [],
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    return out


def write_outcome_feature_packet(private_root: Path, packet: OutcomeFeaturePacket) -> Path:
    out = outcome_packet_dir(private_root) / f"{packet.outcome_packet_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(packet.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    append_jsonl(
        outcome_packet_index_path(private_root),
        {
            "schema": "OutcomeFeaturePacketIndex.v1",
            "outcome_packet_id": packet.outcome_packet_id,
            "decision_packet_id": packet.decision_packet_id,
            "data_packet_id": packet.data_packet_id,
            "scanner_event_id": packet.scanner_event_id,
            "symbol": packet.symbol,
            "timeframe": packet.timeframe,
            "path": str(out),
            "decision_role_eligible": False,
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    return out
