# -*- coding: utf-8 -*-
"""Microstructure outcome memory + orderbook event detector (Theme 40, research-only).

Rejected microstructure outcomes are knowledge, not trash. This module:
  * detect_orderbook_events(): scan recorded public book snapshots for orderbook-pressure events
    (strong top-N imbalance + a dominant near-mid wall), emit a typed event (symbol, ts, side,
    features, threshold_version, source) — NO look-ahead (an event at snapshot k uses only k and
    earlier), NO paper_forward_ready field;
  * summarize_micro(): unify the lane's outcomes into buckets — the tape-replay verdict (real data),
    the recorder readiness (forward orderbook data), and any detected orderbook events.

Closed bucket vocabulary (none is edge or paper-ready):
  followthrough_observed | weak_followthrough | valid_pressure_but_bad_exit | known_bad_wall |
  spread_too_wide | fake_wall_cancel | late_entry | needs_more_samples
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from src.research_lab.micro_features import (
    liquidity_wall,
    orderbook_imbalance,
    spread_bps,
    wall_sequence_features,
)

MICRO_BUCKETS = (
    "followthrough_observed", "weak_followthrough", "valid_pressure_but_bad_exit", "known_bad_wall",
    "spread_too_wide", "fake_wall_cancel", "late_entry", "needs_more_samples",
)
THRESHOLD_VERSION = "v1"
OBI_THRESH = 0.5          # strong top-5 imbalance
WALL_DIST_MAX_BPS = 20.0  # the wall must sit near mid to matter
SPREAD_MAX_BPS = 5.0      # wider than this -> spread_too_wide (rejected, not traded)
MIN_EVENTS = 20           # below this the orderbook outcome is needs_more_samples


def detect_orderbook_events(snapshots: list[dict[str, Any]], symbol: str, *,
                            threshold_version: str = THRESHOLD_VERSION) -> list[dict[str, Any]]:
    """Typed orderbook-pressure events from a time-ordered list of recorded book snapshots (no look-ahead)."""
    events: list[dict[str, Any]] = []
    wall_hist: list[dict[str, Any]] = []
    for snap in snapshots:
        book = {"bids": (snap.get("book") or {}).get("bids") or [], "asks": (snap.get("book") or {}).get("asks") or []}
        if not book["bids"] or not book["asks"]:
            continue
        obi = orderbook_imbalance(book, depth=5)
        spr = spread_bps(book)
        heavy_ask = obi <= -OBI_THRESH
        heavy_bid = obi >= OBI_THRESH
        if not (heavy_ask or heavy_bid):
            wall_hist.append({"present": False})
            continue
        side = "short" if heavy_ask else "long"
        wall = liquidity_wall(book, "ask" if heavy_ask else "bid")
        wall_hist.append(wall)
        reason = "ok"
        if spr > SPREAD_MAX_BPS:
            reason = "spread_too_wide"
        elif not wall.get("present") or wall.get("distance_bps", 1e9) > WALL_DIST_MAX_BPS:
            reason = "known_bad_wall"
        seq = wall_sequence_features(wall_hist[-10:])
        events.append({
            "symbol": symbol, "ts": (snap.get("book") or {}).get("ts") or snap.get("recv_ms"),
            "side": side, "threshold_version": threshold_version, "reason": reason,
            "features": {"obi_top5": obi, "spread_bps": spr,
                         "wall_notional": wall.get("notional"), "wall_distance_bps": wall.get("distance_bps"),
                         "wall_persistence": seq["persistence"], "wall_movement": seq["movement"],
                         "spoof_cancel": seq["spoof_cancel"]},
            "source": "micro_recorder",
        })
    return events


def _read_recordings(private_root: Path, *, limit_files: int | None = None) -> dict[str, list[dict[str, Any]]]:
    out_dir = Path(private_root) / "microstructure" / "recordings"
    by_sym: dict[str, list[dict[str, Any]]] = {}
    files = sorted(out_dir.rglob("*.jsonl.gz")) if out_dir.exists() else []
    for p in (files[:limit_files] if limit_files else files):
        sym = p.stem.replace(".jsonl", "")
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    by_sym.setdefault(sym, []).append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            continue
    return by_sym


def scan_recordings(private_root: Path) -> list[dict[str, Any]]:
    private_root = Path(private_root)
    events: list[dict[str, Any]] = []
    for sym, snaps in _read_recordings(private_root).items():
        snaps.sort(key=lambda s: int(s.get("recv_ms") or 0))
        events.extend(detect_orderbook_events(snaps, sym))
    return events


def write_events(private_root: Path, events: list[dict[str, Any]]) -> Path:
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "microstructure_events.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _orderbook_bucket(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    for ev in events:
        by_reason[ev["reason"]] = by_reason.get(ev["reason"], 0) + 1
    spoofs = sum(1 for ev in events if (ev.get("features") or {}).get("spoof_cancel"))
    bucket = "needs_more_samples" if len(events) < MIN_EVENTS else "events_detected_no_outcome_yet"
    return {"events": len(events), "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
            "spoof_cancel_flags": spoofs, "bucket": bucket}


def summarize_micro(private_root: Path) -> dict[str, Any]:
    """Unified micro-lane outcome view: real-tape verdict + recorder readiness + orderbook events."""
    from src.research_lab.micro_recorder import status as recorder_status
    private_root = Path(private_root)
    derived = private_root / "state" / "derived"
    try:
        tape = json.loads((derived / "micro_tape_replay.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        tape = {}
    tape_summary = tape.get("summary") or {}
    try:
        ob_replay = (json.loads((derived / "micro_orderbook_replay.json").read_text(encoding="utf-8"))
                     .get("summary") or {})
    except (OSError, json.JSONDecodeError):
        ob_replay = {}
    events = scan_recordings(private_root)
    return {
        "tape_sub_lane": {"events": tape.get("events"), "overall_bucket": tape_summary.get("overall_bucket"),
                          "by_side": {k: {"bucket": v.get("bucket"), "median_net_pct": v.get("median_net_pct")}
                                      for k, v in (tape_summary.get("by_side") or {}).items()}},
        "orderbook_sub_lane": {**_orderbook_bucket(events), "recorder": recorder_status(private_root),
                               "followthrough_replay": {"events": ob_replay.get("events_scored"),
                                                        "overall_bucket": ob_replay.get("overall_bucket"),
                                                        "horizon_curve": ob_replay.get("horizon_curve_all")}},
        "buckets_vocabulary": list(MICRO_BUCKETS),
        "note": "research-only microstructure lane; tape-pressure has no follow-through (real data); "
                "orderbook walls pending forward collection; nothing is edge or paper-ready",
    }


def write_snapshot(private_root: Path) -> Path:
    private_root = Path(private_root)
    events = scan_recordings(private_root)
    write_events(private_root, events)
    out_dir = private_root / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "micro_memory.json"
    payload = {"schema": "micro_memory.v1",
               "disclaimer": "Microstructure outcome memory (Theme 40). Research-only; tape-pressure = "
                             "weak_followthrough on real data; orderbook walls pending collection; "
                             "nothing is edge or paper-ready.",
               "summary": summarize_micro(private_root)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Microstructure outcome memory + orderbook event detector.")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    print(json.dumps(summarize_micro(Path(args.private_root)), ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root)))


if __name__ == "__main__":
    main()
