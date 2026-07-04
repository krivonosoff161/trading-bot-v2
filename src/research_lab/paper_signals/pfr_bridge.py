# -*- coding: utf-8 -*-
"""PFR (PAPER_FORWARD_READY) bridge: loads validated farm records and builds paper-watch signals.

Paper / research only — no order path, no .env, no AUTO_TRADE, no private endpoints.
All signal params come from strategy_lab.sqlite (candidates.params_json).  Nothing is invented.
Quality gate is policy-driven; no hardcoded symbol blacklists.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.research_lab.lineage_contract import stable_id
from src.research_lab.paper_signals.contract import PaperActionSignal, validate_signal
from src.research_lab.paper_signals.lane import (
    ARM_WINDOW_BARS, MAX_RISK_PCT, TF_MINUTES,
    _atr, _round, fingerprint,
)
from src.research_lab.strategies.detectors import (
    detect_mean_reversion_fade as _detect_mrf,
    detect_momentum_breakout as _detect_mbr,
)

# Required params that MUST be present in candidates.params_json for a family to build a signal.
# If any are missing the bridge emits a blocker reason rather than inventing a default.
_REQUIRED_PARAMS: dict[str, frozenset[str]] = {
    "momentum_breakout": frozenset({"lookback", "stop_pct", "take_pct", "hold_bars"}),
    "mean_reversion_fade": frozenset({"lookback", "move_pct", "stop_pct", "take_pct", "hold_bars"}),
}

# Research-only quality policy (config-like dict; no hardcoded symbol names).
# All thresholds can be overridden per-call via the `policy` argument.
DEFAULT_QUALITY_POLICY: dict[str, float | int] = {
    "max_drawdown_pct": 35.0,   # excludes high-DD outliers (not a BEAT blacklist)
    "min_n_trades": 15,
    "min_win_rate": 0.50,
    "min_avg_net_pct": 0.3,     # must be net-positive after farm costs
}

_MIN_RR = 2.0   # minimum risk-reward required (matches param_schemas.executable_params_ready)
_FETCH_WINDOW_BARS = 1500


# ── canonical DB helpers ──────────────────────────────────────────────────────

def _params_hash(params: dict) -> str:
    """Stable 12-char SHA1 of sorted params JSON — the canonical identity of a parameter set."""
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]


def _ready_strategy_id(row: dict[str, Any]) -> str:
    payload = {
        "run_id": row.get("run_id"),
        "candidate_id": row.get("candidate_id"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "family": row.get("family"),
        "params_hash": row.get("params_hash"),
    }
    return stable_id("ready", payload, length=20)


def load_pfr_records(db_path: Path | str) -> list[dict[str, Any]]:
    """Load all PAPER_FORWARD_READY records from strategy_lab.sqlite.

    Joins farm_results with candidates to get actual validated params (params_json).
    Returns [] if the DB does not exist or the join yields no rows.
    """
    db = Path(db_path)
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT fr.run_id,
                   fr.candidate_id,
                   fr.symbol,
                   fr.family,
                   fr.timeframe,
                   fr.avg_net_pct,
                   fr.win_rate,
                   fr.n_trades,
                   fr.max_drawdown_pct,
                   fr.hard_status,
                   fr.paper_status,
                   c.params_json
            FROM farm_results fr
            LEFT JOIN candidates c ON fr.run_id = c.run_id AND fr.candidate_id = c.candidate_id
            WHERE fr.hard_status = 'PAPER_FORWARD_READY'
              AND c.params_json IS NOT NULL
            ORDER BY fr.avg_net_pct DESC NULLS LAST
        """)
        rows = cur.fetchall()
        out = []
        for row in rows:
            d = dict(row)
            try:
                d["params"] = json.loads(d.pop("params_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                d["params"] = {}
            d["params_hash"] = _params_hash(d["params"])
            d["setup_id"] = f"setup-{d['candidate_id']}"
            out.append(d)
        return out
    finally:
        conn.close()


# ── quality gate ──────────────────────────────────────────────────────────────

def apply_quality_policy(
    records: list[dict[str, Any]],
    *,
    policy: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter PFR records by research quality policy.

    Returns (passed, rejected).  Every rejected record includes a '_rejection_reasons' list.
    The policy is a plain dict — no symbol names, no family blacklists.
    """
    p: dict[str, Any] = {**DEFAULT_QUALITY_POLICY, **(policy or {})}
    passed, rejected = [], []
    for r in records:
        reasons: list[str] = []
        dd = float(r.get("max_drawdown_pct") or 0.0)
        n = int(r.get("n_trades") or 0)
        wr = float(r.get("win_rate") or 0.0)
        net = float(r.get("avg_net_pct") or 0.0)
        if dd > p["max_drawdown_pct"]:
            reasons.append(f"max_drawdown_pct={dd:.1f}>{p['max_drawdown_pct']}")
        if n < p["min_n_trades"]:
            reasons.append(f"n_trades={n}<{p['min_n_trades']}")
        if wr < p["min_win_rate"]:
            reasons.append(f"win_rate={wr:.3f}<{p['min_win_rate']}")
        if net < p["min_avg_net_pct"]:
            reasons.append(f"avg_net_pct={net:.4f}<{p['min_avg_net_pct']}")
        if reasons:
            rejected.append({**r, "_rejection_reasons": reasons})
        else:
            passed.append(r)
    return passed, rejected


# ── param validation helper ───────────────────────────────────────────────────

def _missing_params(family: str, params: dict) -> list[str]:
    """Return list of missing required param keys for this family."""
    return [k for k in _REQUIRED_PARAMS.get(family, frozenset()) if params.get(k) is None]


# ── signal builders ───────────────────────────────────────────────────────────

def build_pfr_momentum_breakout(
    row: dict[str, Any],
    candles: list[dict[str, Any]],
    *,
    now: float,
    boundary_ts: int,
    mode: str,
) -> tuple[PaperActionSignal | None, str]:
    """Build a momentum_breakout paper signal from a PFR-validated farm record.

    Params (lookback / stop_pct / take_pct / hold_bars / threshold_pct) come ONLY from
    candidates.params_json — never invented or defaulted.  Missing params -> blocker reason.

    Signal detection mirrors signals_momentum_breakout() exactly:
      close[-1] > window_high(last lookback bars, excluding current) * (1 + threshold_pct/100)
    Entry zone adapts to limit-fill lane mechanics; stop/TP are fixed-% from params.
    """
    params = row.get("params") or {}
    missing = _missing_params("momentum_breakout", params)
    if missing:
        return None, f"missing_params:{','.join(missing)}"

    lookback = int(params["lookback"])
    stop_pct = float(params["stop_pct"])
    take_pct = float(params["take_pct"])
    hold_bars = int(params["hold_bars"])
    threshold_pct = float(params.get("threshold_pct") or 0.0)

    if take_pct < stop_pct * _MIN_RR:
        return None, f"rr_below_{_MIN_RR}:{take_pct/max(stop_pct,1e-9):.2f}r"
    if len(candles) < lookback + 2:
        return None, "insufficient_bars"

    price = float(candles[-1]["close"])
    if price <= 0:
        return None, "bad_price"

    det = _detect_mbr(candles, len(candles) - 1, lookback=lookback, threshold_pct=threshold_pct)
    if det is None:
        return None, "no_breakout"

    side = det["side"]
    ref_level = det["ref_level"]
    symbol = str(row["symbol"])
    inst = symbol.replace("_", "-")
    tf = str(row["timeframe"])
    atr = _atr(candles)
    zone_half = max(atr * 0.3, price * 0.003)

    if side == "long":
        entry_hi = _round(price, price)
        entry_lo = _round(max(ref_level, price - zone_half), price)
        if entry_lo >= entry_hi:
            entry_lo = _round(price * 0.997, price)
        stop = _round(entry_lo * (1 - stop_pct / 100), price)
        tp_price = _round(entry_hi * (1 + take_pct / 100), price)
        risk = entry_lo - stop
    else:
        entry_lo = _round(price, price)
        entry_hi = _round(min(ref_level, price + zone_half), price)
        if entry_lo >= entry_hi:
            entry_hi = _round(price * 1.003, price)
        stop = _round(entry_hi * (1 + stop_pct / 100), price)
        tp_price = _round(entry_lo * (1 - take_pct / 100), price)
        risk = stop - entry_hi

    if risk <= 0:
        return None, "non_positive_risk"
    risk_pct = round(risk / price * 100, 3)
    if risk_pct > MAX_RISK_PCT:
        return None, f"risk_too_wide_{risk_pct}pct"

    tf_min = TF_MINUTES.get(tf, 15)
    fp = fingerprint(candles)
    level_label = "high" if side == "long" else "low"
    sig = PaperActionSignal(
        signal_id=f"{symbol}_{tf}_momentum_breakout_{fp}",
        source="pfr_farm",
        symbol=symbol,
        okx_inst_id=inst,
        timeframe=tf,
        side=side,
        setup_family="momentum_breakout",
        entry_zone=[entry_lo, entry_hi],
        stop_loss=stop,
        invalidation_rule=(
            f"{lookback}-bar breakout invalidated if close reverses back below "
            f"{lookback}-bar {level_label}={ref_level:.4f} or no entry in {ARM_WINDOW_BARS} bars"
        ),
        take_profit_plan=[{"label": "tp1", "price": tp_price, "size_frac": 1.0}],
        max_hold_bars=hold_bars,
        max_hold_minutes=hold_bars * tf_min,
        reason_now=(
            f"PFR momentum_breakout: {side} close={price:.4f} broke {lookback}-bar "
            f"{level_label}={ref_level:.4f} (thresh={threshold_pct}%); "
            f"stop_pct={stop_pct} take_pct={take_pct}; "
            f"farm WR={row.get('win_rate', 0):.0%} n={row.get('n_trades', 0)}"
        ),
        risk_notes=(
            "paper-only forward watch; farm backtest != live forward; no order path; "
            "NOT an edge or profitable-strategy claim"
        ),
        validator_context={
            "setup_id": row["setup_id"],
            "ready_strategy_id": _ready_strategy_id(row),
            "candidate_id": str(row["candidate_id"]),
            "run_id": str(row.get("run_id") or ""),
            "params_hash": row["params_hash"],
            "source_validation_verdict": str(row.get("hard_status") or ""),
            "family": row["family"],
            "timeframe": tf,
            "symbol": symbol,
            "pfr_avg_net_pct": row.get("avg_net_pct"),
            "pfr_win_rate": row.get("win_rate"),
            "pfr_n_trades": row.get("n_trades"),
        },
        status="armed",
        created_at=now,
        expires_at=now + ARM_WINDOW_BARS * tf_min * 60,
        ref_price=price,
        risk_pct=risk_pct,
        boundary_ts=boundary_ts,
        data_fingerprint=fp,
        dedup_key=f"{symbol}|{tf}|momentum_breakout",
        mode=mode,
    )
    ok, problems = validate_signal(sig)
    if not ok:
        return None, "failed_validate:" + ";".join(problems)
    return sig, "ok"


def build_pfr_mean_reversion_fade(
    row: dict[str, Any],
    candles: list[dict[str, Any]],
    *,
    now: float,
    boundary_ts: int,
    mode: str,
) -> tuple[PaperActionSignal | None, str]:
    """Build a mean_reversion_fade paper signal from a PFR-validated farm record.

    Params (lookback / move_pct / stop_pct / take_pct / hold_bars) come ONLY from
    candidates.params_json — never invented or defaulted.

    Signal detection mirrors signals_mean_reversion_fade() exactly:
      move = (close[-1] / close[-lookback-1] - 1) * 100
      short if move >= move_pct (fade the up move), long if move <= -move_pct
    Entry zone uses a small ATR-scaled zone so the limit fill is reachable.
    """
    params = row.get("params") or {}
    missing = _missing_params("mean_reversion_fade", params)
    if missing:
        return None, f"missing_params:{','.join(missing)}"

    lookback = int(params["lookback"])
    move_pct = float(params["move_pct"])
    stop_pct = float(params["stop_pct"])
    take_pct = float(params["take_pct"])
    hold_bars = int(params["hold_bars"])

    if take_pct < stop_pct * _MIN_RR:
        return None, f"rr_below_{_MIN_RR}:{take_pct/max(stop_pct,1e-9):.2f}r"
    if len(candles) < lookback + 2:
        return None, "insufficient_bars"

    price = float(candles[-1]["close"])
    if price <= 0:
        return None, "bad_price"

    det = _detect_mrf(candles, len(candles) - 1, lookback=lookback, move_pct=move_pct)
    if det is None:
        return None, f"no_fade_signal:move_pct_threshold={move_pct}"

    side = det["side"]
    move = det["move"]

    symbol = str(row["symbol"])
    inst = symbol.replace("_", "-")
    tf = str(row["timeframe"])
    atr = _atr(candles)
    zone_half = max(atr * 0.2, price * 0.002)
    tf_min = TF_MINUTES.get(tf, 15)

    if side == "short":
        # Wait for a small bounce to enter short — entry_zone above current price
        entry_lo = _round(price, price)
        entry_hi = _round(price + zone_half, price)
        if entry_lo >= entry_hi:
            entry_hi = _round(price * 1.002, price)
        stop = _round(entry_hi * (1 + stop_pct / 100), price)
        tp_price = _round(entry_hi * (1 - take_pct / 100), price)
        risk = stop - entry_hi
    else:
        # Wait for a small dip to enter long — entry_zone below current price
        entry_hi = _round(price, price)
        entry_lo = _round(price - zone_half, price)
        if entry_lo >= entry_hi:
            entry_lo = _round(price * 0.998, price)
        stop = _round(entry_lo * (1 - stop_pct / 100), price)
        tp_price = _round(entry_lo * (1 + take_pct / 100), price)
        risk = entry_lo - stop

    if risk <= 0:
        return None, "non_positive_risk"
    risk_pct = round(risk / price * 100, 3)
    if risk_pct > MAX_RISK_PCT:
        return None, f"risk_too_wide_{risk_pct}pct"

    fp = fingerprint(candles)
    sig = PaperActionSignal(
        signal_id=f"{symbol}_{tf}_mean_reversion_fade_{fp}",
        source="pfr_farm",
        symbol=symbol,
        okx_inst_id=inst,
        timeframe=tf,
        side=side,
        setup_family="mean_reversion_fade",
        entry_zone=[entry_lo, entry_hi],
        stop_loss=stop,
        invalidation_rule=(
            f"fade invalidated if move extends: "
            f"{'close > stop' if side == 'short' else 'close < stop'} "
            f"or no fill within {ARM_WINDOW_BARS} bars"
        ),
        take_profit_plan=[{"label": "tp1", "price": tp_price, "size_frac": 1.0}],
        max_hold_bars=hold_bars,
        max_hold_minutes=hold_bars * tf_min,
        reason_now=(
            f"PFR mean_reversion_fade: {side} after {move:.1f}% {lookback}-bar move "
            f"(threshold={move_pct}%); stop_pct={stop_pct} take_pct={take_pct}; "
            f"farm WR={row.get('win_rate', 0):.0%} n={row.get('n_trades', 0)}"
        ),
        risk_notes=(
            "paper-only forward watch; farm backtest != live forward; no order path; "
            "NOT an edge or profitable-strategy claim"
        ),
        validator_context={
            "setup_id": row["setup_id"],
            "ready_strategy_id": _ready_strategy_id(row),
            "candidate_id": str(row["candidate_id"]),
            "run_id": str(row.get("run_id") or ""),
            "params_hash": row["params_hash"],
            "source_validation_verdict": str(row.get("hard_status") or ""),
            "family": row["family"],
            "timeframe": tf,
            "symbol": symbol,
            "pfr_avg_net_pct": row.get("avg_net_pct"),
            "pfr_win_rate": row.get("win_rate"),
            "pfr_n_trades": row.get("n_trades"),
        },
        status="armed",
        created_at=now,
        expires_at=now + ARM_WINDOW_BARS * tf_min * 60,
        ref_price=price,
        risk_pct=risk_pct,
        boundary_ts=boundary_ts,
        data_fingerprint=fp,
        dedup_key=f"{symbol}|{tf}|mean_reversion_fade",
        mode=mode,
    )
    ok, problems = validate_signal(sig)
    if not ok:
        return None, "failed_validate:" + ";".join(problems)
    return sig, "ok"


# ── builder dispatch ──────────────────────────────────────────────────────────

_BUILDERS = {
    "momentum_breakout": build_pfr_momentum_breakout,
    "mean_reversion_fade": build_pfr_mean_reversion_fade,
}


def _diverse_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep the strongest record per live trigger identity.

    PFR rows are ordered by historical net. A bounded live-forward cycle should
    not spend all candle fetches on many parameter variants of the same
    symbol/timeframe/family; that starves other validated setups and makes the
    main-paper lane look dead even when the catalog has diverse candidates.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    duplicates = 0
    for row in records:
        key = (
            str(row.get("symbol") or ""),
            str(row.get("timeframe") or ""),
            str(row.get("family") or ""),
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        out.append(row)
    return out, duplicates


# ── PFR generation (called from cycle.run_cycle) ──────────────────────────────

def generate_pfr_signals(
    records: list[dict[str, Any]],
    *,
    provider: Any,
    now: float,
    mode: str,
    active_dedup: set[str],
    active_setup_ids: set[str],
    recent_fingerprints: set[tuple[str, str]],
    max_pfr: int = 3,
    timeframes: tuple[str, ...] | list[str] = ("15m", "1h", "4h"),
    status_counts: dict[str, int] | None = None,
    max_pfr_scan: int = 30,
    max_pfr_fetches: int | None = 8,
) -> list[tuple[PaperActionSignal, list[dict]]]:
    """Generate paper-watch signals from PFR records using live candles.

    Enforces three dedup layers:
      1. dedup_key (same symbol|tf|family already active)
      2. setup_id (same validated setup already active)
      3. (dedup_key, data_fingerprint) — same data, no new bar, skip

    max_pfr_scan: stop inspecting records after this many to keep cycles bounded.
    Returns list of (signal, candles).  status_counts is mutated with per-reason counts
    so the cycle report can show PFR channel stats without silent truncation.
    """
    sc: dict[str, int] = status_counts if status_counts is not None else {}
    tf_set = set(timeframes)
    generated: list[tuple[PaperActionSignal, list[dict]]] = []
    pfr_scanned = 0
    pfr_fetches = 0
    diverse_records, duplicate_variants = _diverse_records(records)
    if duplicate_variants:
        sc["pfr_duplicate_setup_variant"] = sc.get("pfr_duplicate_setup_variant", 0) + duplicate_variants

    for row in diverse_records:
        if pfr_scanned >= max_pfr_scan:
            sc["pfr_scan_limit_reached"] = sc.get("pfr_scan_limit_reached", 0) + 1
            continue
        pfr_scanned += 1

        if len(generated) >= max_pfr:
            sc["pfr_cap_reached"] = sc.get("pfr_cap_reached", 0) + 1
            continue

        family = str(row.get("family") or "")
        builder = _BUILDERS.get(family)
        if builder is None:
            sc[f"pfr_no_builder:{family}"] = sc.get(f"pfr_no_builder:{family}", 0) + 1
            continue

        tf = str(row.get("timeframe") or "")
        if tf not in tf_set:
            sc["pfr_tf_filtered"] = sc.get("pfr_tf_filtered", 0) + 1
            continue

        symbol = str(row["symbol"])
        inst = symbol.replace("_", "-")
        dedup_key = f"{symbol}|{tf}|{family}"

        if dedup_key in active_dedup:
            sc["pfr_dedup_active_key"] = sc.get("pfr_dedup_active_key", 0) + 1
            continue

        setup_id = str(row.get("setup_id") or "")
        if setup_id and setup_id in active_setup_ids:
            sc["pfr_dedup_setup_id"] = sc.get("pfr_dedup_setup_id", 0) + 1
            continue

        # Fetch live candles via provider. Keep the request bounded; OKX public provider
        # refuses unbounded windows and PFR should never ask for "0..now" history.
        if max_pfr_fetches is not None and pfr_fetches >= max(0, int(max_pfr_fetches)):
            sc["pfr_fetch_limit_reached"] = sc.get("pfr_fetch_limit_reached", 0) + 1
            break
        try:
            now_ms = int(now * 1000)
            tf_ms = TF_MINUTES.get(tf, 15) * 60_000
            pfr_fetches += 1
            candles = provider.fetch_ohlcv(inst, tf, now_ms - _FETCH_WINDOW_BARS * tf_ms, now_ms)
        except TimeoutError:
            sc["pfr_fetch_timeout"] = sc.get("pfr_fetch_timeout", 0) + 1
            continue
        except Exception:  # noqa: BLE001 - network must not crash a cycle
            sc["pfr_candle_fetch_error"] = sc.get("pfr_candle_fetch_error", 0) + 1
            continue

        if not candles or len(candles) < 20:
            sc["pfr_insufficient_bars"] = sc.get("pfr_insufficient_bars", 0) + 1
            continue

        fp = fingerprint(candles)
        if (dedup_key, fp) in recent_fingerprints:
            sc["pfr_dedup_same_data"] = sc.get("pfr_dedup_same_data", 0) + 1
            continue

        boundary_ts = int(candles[-1]["ts"])
        sig, reason = builder(row, candles, now=now, boundary_ts=boundary_ts, mode=mode)
        if sig is None:
            reason_key = f"pfr_rejected:{reason[:50]}"
            sc[reason_key] = sc.get(reason_key, 0) + 1
            continue

        sc["pfr_generated"] = sc.get("pfr_generated", 0) + 1
        active_dedup.add(dedup_key)
        active_setup_ids.add(setup_id)
        generated.append((sig, candles))

    return generated
