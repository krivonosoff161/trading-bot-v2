"""Reproducible market-data packets for paper/research events."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import append_jsonl, stable_id, utc_now
from src.research_lab.pipeline_policy import default_caps

SCHEMA = "MarketDataPacket.v1"

WINDOWS = {
    "15m": {"back_min": 96, "back_max": 192, "forward_min": 32, "forward_max": 96},
    "1h": {"back_min": 120, "back_max": 240, "forward_min": 24, "forward_max": 72},
    "4h": {"back_min": 120, "back_max": 180, "forward_min": 12, "forward_max": 48},
    "1d": {"back_min": 180, "back_max": 365, "forward_min": 5, "forward_max": 30},
}


def packet_dir(private_root: Path) -> Path:
    return Path(private_root) / "market_data" / "packets"


def packet_index_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "lineage" / "data_packets.jsonl"


def _fingerprint(candles: list[dict[str, Any]]) -> str:
    payload = {
        "n": len(candles),
        "first": candles[0].get("ts") if candles else None,
        "last": candles[-1].get("ts") if candles else None,
        "tail": [(c.get("ts"), c.get("close")) for c in candles[-8:]],
    }
    return stable_id("cdfp", payload, length=12)


def split_window(candles: list[dict[str, Any]], timeframe: str, mode: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if mode not in {"live", "replay", "validation", "backfill"}:
        raise ValueError(f"unsupported packet mode: {mode}")
    spec = WINDOWS.get(timeframe, WINDOWS["15m"])
    caps = default_caps()
    capped = candles[-caps.max_candles_per_packet:]
    back_n = min(int(spec["back_max"]), caps.max_candles_per_packet)
    if mode == "live":
        return capped[-back_n:], []
    forward_n = int(spec["forward_max"])
    if len(capped) <= back_n:
        return capped, []
    return capped[-(back_n + forward_n):-forward_n], capped[-forward_n:]


@dataclass(frozen=True)
class MarketDataPacket:
    data_packet_id: str
    scanner_event_id: str
    symbol: str
    instrument: str
    timeframe: str
    mode: str
    ohlcv_window: list[dict[str, Any]]
    future_window: list[dict[str, Any]] = field(default_factory=list)
    scanner_reason: str = ""
    liquidity: dict[str, Any] = field(default_factory=dict)
    context_refs: dict[str, Any] = field(default_factory=dict)
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    data_quality_flags: list[str] = field(default_factory=list)
    no_lookahead: bool = True
    created_at: str = field(default_factory=utc_now)
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_market_data_packet(
    *,
    scanner_event_id: str,
    symbol: str,
    instrument: str,
    timeframe: str,
    mode: str,
    candles: list[dict[str, Any]],
    scanner_reason: str = "",
    liquidity: dict[str, Any] | None = None,
    context_refs: dict[str, Any] | None = None,
    provider_name: str = "okx-public",
) -> MarketDataPacket:
    if mode not in {"live", "replay", "validation", "backfill"}:
        raise ValueError(f"unsupported packet mode: {mode}")
    past, future = split_window(candles, timeframe, mode)
    flags: list[str] = []
    spec = WINDOWS.get(timeframe, WINDOWS["15m"])
    if len(past) < int(spec["back_min"]):
        flags.append("short_history")
    if mode == "live" and future:
        flags.append("live_future_window_blocked")
        future = []
    if not past:
        flags.append("empty_ohlcv_window")
    payload = {
        "scanner_event_id": scanner_event_id,
        "symbol": symbol,
        "instrument": instrument,
        "timeframe": timeframe,
        "mode": mode,
        "fingerprint": _fingerprint(past),
    }
    return MarketDataPacket(
        data_packet_id=stable_id("mdp", payload),
        scanner_event_id=scanner_event_id,
        symbol=symbol,
        instrument=instrument,
        timeframe=timeframe,
        mode=mode,
        ohlcv_window=past,
        future_window=future,
        scanner_reason=scanner_reason,
        liquidity=liquidity or {},
        context_refs=context_refs or {},
        provider_metadata={
            "provider": provider_name,
            "window_policy": spec,
            "past_bars": len(past),
            "future_bars": len(future),
        },
        data_quality_flags=flags,
        no_lookahead=(mode == "live" and not future),
    )


def write_market_data_packet(private_root: Path, packet: MarketDataPacket) -> Path:
    out = packet_dir(private_root) / f"{packet.data_packet_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_jsonl(
        packet_index_path(private_root),
        {
            "schema": "MarketDataPacketIndex.v1",
            "data_packet_id": packet.data_packet_id,
            "scanner_event_id": packet.scanner_event_id,
            "symbol": packet.symbol,
            "instrument": packet.instrument,
            "timeframe": packet.timeframe,
            "mode": packet.mode,
            "path": str(out),
            "past_bars": len(packet.ohlcv_window),
            "future_bars": len(packet.future_window),
            "data_quality_flags": packet.data_quality_flags,
            "paper_only": True,
            "execution_allowed": False,
        },
    )
    return out
