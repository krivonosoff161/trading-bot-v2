# -*- coding: utf-8 -*-
"""Repeatable paper-watch CYCLE: observe -> close -> review -> remember -> generate (research-only).

One cycle = (1) re-observe every active signal on FRESH bars and close/expire the terminal ones, writing a
visual review + an outcome-memory row; (2) generate new signals from candidates, deduplicated by
dedup_key + data_fingerprint so repeated runs never spam duplicates and a known-bad setup is not reborn on
the same data; (3) persist the snapshot + a status JSON. Bounded N-cycle loop, stop-file aware.

Live vs replay: a "live" signal pins boundary=now and matures forward over real cycles; a "replay" signal
pins the boundary back and resolves immediately on already-elapsed bars — a labelled diagnostic, never
mixed with true-forward. No order/.env/live path anywhere.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.research_lab.paper_signals import families, lane, store
from src.research_lab.paper_signals.contract import PaperActionSignal

REGEN_TTL_SECONDS = 3600        # do not regenerate the same dedup_key within this window
TERMINAL = ("closed_paper", "expired", "reviewed", "invalidated")
ACTIVE = ("candidate", "armed", "opened_paper")


def _memory_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_signal_memory.jsonl"


def _status_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_signals_status.json"


def record_memory(private_root: Path, sig: PaperActionSignal) -> None:
    """Append a terminal outcome as a learning row (research-only knowledge, not edge)."""
    row = {"ts": round(sig.created_at, 1), "dedup_key": sig.dedup_key, "symbol": sig.symbol,
           "timeframe": sig.timeframe, "family": sig.setup_family, "side": sig.side,
           "data_fingerprint": sig.data_fingerprint, "mode": sig.mode,
           "result": (sig.outcome or {}).get("result"), "net_pct": (sig.outcome or {}).get("net_pct"),
           "net_r": (sig.review or {}).get("net_r"), "diagnosis": (sig.review or {}).get("diagnosis")}
    path = _memory_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_memory(private_root: Path) -> list[dict[str, Any]]:
    path = _memory_path(private_root)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _fetch(provider, symbol: str, tf: str, now_ms: int) -> list[dict[str, Any]]:
    bars_ms = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}.get(tf, 900_000)
    try:
        return provider.fetch_ohlcv(symbol, tf, now_ms - 1500 * bars_ms, now_ms)
    except Exception:  # noqa: BLE001 - network must not crash a cycle
        return []


def _load_movers(private_root: Path) -> list[dict]:
    try:
        data = json.loads((private_root / "discovery" / "live_universe.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows: list[dict] = []
    for grp in (data.get("detail") or {}).values():
        rows.extend(grp or [])
    rows.sort(key=lambda r: -(r.get("score") or 0))
    return rows


def run_cycle(private_root: Path, *, mode: str = "live", timeframes=("15m", "1h"), max_new=5,
              apply: bool = False, provider=None, now: float | None = None,
              families_arg=None) -> dict[str, Any]:
    private_root = Path(private_root)
    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    now = time.time() if now is None else now
    now_ms = int(now * 1000)
    existing = store.load_signals(private_root)
    by_key_active = {s.dedup_key for s in existing if s.status in ACTIVE}
    recent_terminal = {(s.dedup_key, s.data_fingerprint) for s in existing if s.status in TERMINAL}
    last_seen = {s.dedup_key: s.created_at for s in existing}
    known_bad = lane.load_known_bad(private_root)
    learned_bad = learn_known_bad(load_memory(private_root))

    observed, closed = 0, 0
    # (1) re-observe active signals on fresh bars
    for s in existing:
        if s.status not in ("armed", "opened_paper"):
            continue
        candles = _fetch(provider, s.symbol, s.timeframe, now_ms)
        if not candles:
            s.outcome = {"result": "no_data", "new_bars": 0}
            if apply:
                store.update_signal(private_root, s)
            continue
        before = s.status
        s = lane.review(lane.observe(s, candles))
        observed += 1
        if s.status != before and s.status in TERMINAL:
            closed += 1
            if apply:
                store.update_signal(private_root, s)
                lane.write_review_artifact(private_root, s, candles)
                record_memory(private_root, s)
        elif apply:
            store.update_signal(private_root, s)

    # (2) generate new, deduplicated
    movers = _load_movers(private_root)
    gate_counts: dict[str, int] = {}
    new_sigs = []
    for mv in movers:
        if len(new_sigs) >= max_new:
            break
        inst = str(mv.get("inst_id") or "")
        symbol = str(mv.get("symbol") or inst.replace("-", "_"))
        for tf in timeframes:
            if len(new_sigs) >= max_new:
                break
            candles = _fetch(provider, symbol, tf, now_ms)
            if not candles:
                gate_counts["no_data"] = gate_counts.get("no_data", 0) + 1
                continue
            g = lane.gate_candidate({**mv, "_tf": tf}, candles, now_ms=now_ms, known_bad=known_bad)
            gate_counts[g] = gate_counts.get(g, 0) + 1
            if g != "ok":
                continue
            if mode == "replay":
                back = lane.HORIZON_BARS.get(tf, 12) + lane.ARM_WINDOW_BARS + 1
                decide = candles[:-back] if len(candles) > back + 40 else candles
            else:
                decide = candles
            boundary_ts = int(decide[-1]["ts"])
            wo = families.watch_only_reason(decide)
            if wo:
                gate_counts[wo] = gate_counts.get(wo, 0) + 1
                continue
            for sig, fam in families.generate(symbol, inst, tf, decide, mover=mv, now=now,
                                              boundary_ts=boundary_ts, mode=mode, families=families_arg):
                if len(new_sigs) >= max_new:
                    break
                if sig.dedup_key in by_key_active:
                    gate_counts["dedup_active"] = gate_counts.get("dedup_active", 0) + 1
                    continue
                if (symbol, tf, sig.setup_family) in learned_bad:
                    gate_counts["learned_known_bad"] = gate_counts.get("learned_known_bad", 0) + 1
                    continue
                if now - last_seen.get(sig.dedup_key, 0) < REGEN_TTL_SECONDS:
                    gate_counts["regen_ttl"] = gate_counts.get("regen_ttl", 0) + 1
                    continue
                if (sig.dedup_key, sig.data_fingerprint) in recent_terminal:
                    gate_counts["dedup_same_data"] = gate_counts.get("dedup_same_data", 0) + 1
                    continue
                gate_counts[f"family:{fam}"] = gate_counts.get(f"family:{fam}", 0) + 1
                if mode == "replay":
                    sig = lane.review(lane.observe(sig, candles))
                new_sigs.append((sig, candles))
                by_key_active.add(sig.dedup_key)

    if apply:
        for sig, candles in new_sigs:
            store.append_signal(private_root, sig)
            if sig.status in TERMINAL:
                lane.write_review_artifact(private_root, sig, candles)
                record_memory(private_root, sig)
        store.write_state_snapshot(private_root)

    report = {"mode": mode, "apply": apply, "observed": observed, "closed": closed,
              "generated": len(new_sigs), "gate_counts": gate_counts,
              "state": store.current_state_view(private_root)["by_status"] if apply else {},
              "new_cards": [s.signal_id for s, _ in new_sigs]}
    if apply:
        _write_status(private_root, report, now)
    return report


def _write_status(private_root: Path, report: dict[str, Any], now: float) -> Path:
    sigs = store.load_signals(private_root)
    by_status: dict[str, int] = {}
    for s in sigs:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    diag: dict[str, int] = {}
    for m in load_memory(private_root):
        d = m.get("diagnosis")
        if d:
            diag[d] = diag.get(d, 0) + 1
    payload = {"schema": "paper_signals_status.v1", "updated_at": round(now, 1),
               "by_status": by_status, "diagnosis_counts": diag, "last_cycle": report,
               "all_research_only": True, "disclaimer": "paper-watch only; NOT orders; outcome != edge"}
    path = _status_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def learn_known_bad(memory: list[dict[str, Any]], *, min_n: int = 3) -> set[tuple[str, str, str]]:
    """A (symbol, tf, family) becomes 'learned bad' after >= min_n terminal outcomes that are ALL
    losing/no-entry (stop / expired / no_follow_through). Deterministic; feeds the next cycle's gate."""
    agg: dict[tuple[str, str, str], list[str]] = {}
    bad_results = {"stop", "expired_no_entry"}
    bad_diag = {"entry_after_exhaustion", "no_follow_through", "wrong_direction", "late_entry"}
    for m in memory:
        key = (m.get("symbol"), m.get("timeframe"), m.get("family"))
        if None in key:
            continue
        ok = (m.get("result") in bad_results) or (m.get("diagnosis") in bad_diag)
        agg.setdefault(key, []).append("bad" if ok else "good")
    out = set()
    for key, tags in agg.items():
        if len(tags) >= min_n and all(t == "bad" for t in tags):
            out.add(key)
    return out


def run_loop(private_root: Path, *, cycles: int, sleep_seconds: int = 0, stop_file: str = "",
             mode: str = "live", timeframes=("15m", "1h"), max_new=5, provider=None) -> list[dict]:
    reports = []
    for i in range(max(1, cycles)):
        if stop_file and Path(stop_file).exists():
            reports.append({"stopped": True, "at_cycle": i})
            break
        reports.append(run_cycle(private_root, mode=mode, timeframes=timeframes, max_new=max_new,
                                 apply=True, provider=provider))
        if sleep_seconds and i < cycles - 1:
            time.sleep(max(1, sleep_seconds))
    return reports
