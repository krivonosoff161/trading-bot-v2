# -*- coding: utf-8 -*-
"""Repeatable paper-watch CYCLE: observe -> close -> review -> remember -> generate (research-only).

One cycle = (1) re-observe every active signal on FRESH bars and close/expire the terminal ones, writing a
visual review + an outcome-memory row; (2) generate new signals from candidates, deduplicated by
dedup_key + data_fingerprint so repeated runs never spam duplicates and a known-bad setup is not reborn on
the same data; (3) persist the snapshot + a status JSON. Bounded N-cycle loop, stop-file aware.

Live vs replay: a "live" signal pins boundary=now and matures forward over real cycles; a "replay" signal
pins the boundary back and resolves immediately on already-elapsed bars - a labelled diagnostic, never
mixed with true-forward. No order/.env/live path anywhere.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from src.research_lab.feature_packet import build_feature_packet, write_feature_packet
from src.research_lab.lineage_contract import scanner_event_from_mover, write_cycle_link, write_scanner_event
from src.research_lab.market_data_packet import build_market_data_packet, write_market_data_packet
from src.research_lab.paper_signals import families, lane, pfr_bridge, store
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.pipeline_policy import add_reason, default_caps, new_stage_counts

REGEN_TTL_SECONDS = 3600        # do not regenerate the same dedup_key within this window
FETCH_WINDOW_BARS = 220         # enough for ATR/trend/lifecycle, bounded so OKX paging stays cheap
TERMINAL = ("closed_paper", "expired", "reviewed", "invalidated")
ACTIVE = ("candidate", "armed", "opened_paper")


def _memory_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_signal_memory.jsonl"


def _status_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "paper_signals_status.json"


def _pfr_gap_telemetry_path(private_root: Path) -> Path:
    return Path(private_root) / "state" / "derived" / "pfr_gap_telemetry.jsonl"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_pfr_gap_memory(private_root: Path, *, max_cycles: int = 20) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Recent PFR trigger-distance memory used to spend bounded fetches on nearer setups first."""
    path = _pfr_gap_telemetry_path(private_root)
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, int(max_cycles)):]
    except OSError:
        return {}
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        updated_at = float(payload.get("updated_at") or 0.0)
        samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            key = (
                str(sample.get("symbol") or ""),
                str(sample.get("timeframe") or ""),
                str(sample.get("family") or ""),
            )
            if not all(key):
                continue
            gap = _float_or_none(sample.get("min_gap_pct"))
            if gap is None:
                continue
            current = out.get(key)
            if current is None or gap < float(current.get("min_gap_pct") or 999_999.0):
                out[key] = {
                    "min_gap_pct": round(gap, 6),
                    "updated_at": updated_at,
                    "selection_state": str(sample.get("selection_state") or ""),
                    "bucket": str(sample.get("bucket") or ""),
                }
    return out


def prioritize_pfr_records_by_gap_memory(
    records: list[dict[str, Any]],
    gap_memory: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    if not gap_memory:
        return list(records)

    def sort_key(row: dict[str, Any]) -> tuple[int, float, float, str]:
        key = (str(row.get("symbol") or ""), str(row.get("timeframe") or ""), str(row.get("family") or ""))
        memory = gap_memory.get(key)
        if not memory:
            return (1, 999_999.0, -float(row.get("avg_net_pct") or 0.0), str(row.get("candidate_id") or ""))
        return (
            0,
            float(memory.get("min_gap_pct") or 999_999.0),
            -float(row.get("avg_net_pct") or 0.0),
            str(row.get("candidate_id") or ""),
        )

    return sorted(records, key=sort_key)


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


def load_product_memory(private_root: Path) -> dict[str, Any]:
    """Broad paper-product memory for search ranking; best-effort and read-only."""
    try:
        from src.research_lab.setup_outcome_memory import summarize_product_training_memory
        memory = summarize_product_training_memory(Path(private_root))
        calibration_path = Path(private_root) / "state" / "derived" / "trading_policy_calibration.json"
        try:
            calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            calibration = {}
        memory["calibration"] = calibration if isinstance(calibration, dict) else {}
        return memory
    except Exception:  # noqa: BLE001 - search memory must never break the paper loop
        return {}


def _fetch(provider, symbol: str, tf: str, now_ms: int) -> list[dict[str, Any]]:
    bars_ms = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}.get(tf, 900_000)
    try:
        return provider.fetch_ohlcv(symbol, tf, now_ms - FETCH_WINDOW_BARS * bars_ms, now_ms)
    except Exception:  # noqa: BLE001 - network must not crash a cycle
        return []


def _attach_lineage(
    private_root: Path,
    sig: PaperActionSignal,
    candles: list[dict[str, Any]],
    *,
    mode: str,
    provider: Any,
) -> PaperActionSignal:
    """Persist scanner/data/feature lineage for one stored paper signal."""
    ctx = sig.validator_context or {}
    setup_id = str(ctx.get("setup_id") or ctx.get("candidate_id") or "")
    sweep_run_id = str(ctx.get("run_id") or ctx.get("sweep_run_id") or "")
    validation_id = str(ctx.get("validation_id") or "")
    pseudo_mover = {
        "source": sig.source,
        "reason": sig.reason_now,
        "symbol": sig.symbol,
        "inst_id": sig.okx_inst_id,
        "_bucket": ctx.get("asset_group") or ctx.get("group"),
        "score": ctx.get("score"),
        "move_pct": ctx.get("move_pct"),
        "vol_usd": ctx.get("vol_usd"),
        "spread_bps": ctx.get("spread_bps"),
    }
    event = scanner_event_from_mover(
        pseudo_mover,
        symbol=sig.symbol,
        instrument=sig.okx_inst_id,
        timeframe=sig.timeframe,
        mode=mode,
    )
    write_scanner_event(private_root, event)
    data_packet = build_market_data_packet(
        scanner_event_id=event.scanner_event_id,
        symbol=sig.symbol,
        instrument=sig.okx_inst_id,
        timeframe=sig.timeframe,
        mode=mode,
        candles=candles,
        scanner_reason=event.reason,
        liquidity=event.liquidity,
        context_refs=event.context_refs,
        provider_name=str(getattr(provider, "name", "unknown")),
    )
    write_market_data_packet(private_root, data_packet)
    feature_packet = build_feature_packet(
        data_packet,
        side=sig.side,
        entry_zone=sig.entry_zone,
        stop_loss=sig.stop_loss,
        take_profit_plan=sig.take_profit_plan,
    )
    write_feature_packet(private_root, feature_packet)
    sig.scanner_event_id = event.scanner_event_id
    sig.data_packet_id = data_packet.data_packet_id
    sig.feature_packet_id = feature_packet.feature_packet_id
    sig.setup_candidate_id = setup_id
    sig.sweep_run_id = sweep_run_id
    sig.validation_id = validation_id
    write_cycle_link(
        private_root,
        {
            "scanner_event_id": sig.scanner_event_id,
            "data_packet_id": sig.data_packet_id,
            "feature_packet_id": sig.feature_packet_id,
            "setup_candidate_id": sig.setup_candidate_id,
            "sweep_run_id": sig.sweep_run_id,
            "validation_id": sig.validation_id,
            "paper_signal_id": sig.signal_id,
            "source": sig.source,
            "symbol": sig.symbol,
            "instrument": sig.okx_inst_id,
            "timeframe": sig.timeframe,
            "setup_family": sig.setup_family,
            "mode": sig.mode,
        },
    )
    return sig


def _load_movers(private_root: Path) -> list[dict]:
    try:
        data = json.loads((private_root / "discovery" / "live_universe.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows: list[dict] = []
    for grp, lst in (data.get("detail") or {}).items():
        for r in lst or []:
            rows.append({**r, "_bucket": r.get("group") or grp})
    rows.sort(key=lambda r: -(r.get("score") or 0))
    return rows


def _product_scores_by_symbol(product_memory: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    by_cell = (product_memory or {}).get("by_cell") if isinstance(product_memory, dict) else None
    if not isinstance(by_cell, dict):
        return by_symbol
    for key, stats in by_cell.items():
        if not isinstance(stats, dict):
            continue
        symbol = str(key).split("|", 1)[0]
        if not symbol:
            continue
        agg = by_symbol.setdefault(
            symbol,
            {"terminal": 0, "wins": 0, "losses": 0, "gave_back": 0, "pnl": 0.0},
        )
        terminal = int(stats.get("terminal_rows") or 0)
        agg["terminal"] += terminal
        agg["wins"] += int(stats.get("win_rows") or 0)
        agg["losses"] += int(stats.get("loss_rows") or 0)
        agg["gave_back"] += int(stats.get("gave_back_rows") or 0)
        agg["pnl"] = round(float(agg.get("pnl") or 0.0) + float(stats.get("paper_pnl_usdt") or 0.0), 6)
    for symbol, agg in by_symbol.items():
        terminal = int(agg.get("terminal") or 0)
        if terminal < 3:
            agg["score"] = 0.0
            agg["reason"] = "product_memory=thin"
            continue
        wins = int(agg.get("wins") or 0)
        losses = int(agg.get("losses") or 0)
        gave_back = int(agg.get("gave_back") or 0)
        balance = (wins - losses) / terminal
        gave_back_penalty = min(1.0, gave_back / terminal)
        score = max(-3.0, min(2.0, round(balance * 2.0 - gave_back_penalty, 3)))
        avg_pnl = round(float(agg.get("pnl") or 0.0) / terminal, 4)
        agg["score"] = score
        agg["reason"] = (
            f"product_memory={score:+.2f} terminal={terminal} "
            f"w/l={wins}/{losses} avg_pnl={avg_pnl} gave_back={gave_back}"
        )
    return by_symbol


def rank_movers(movers: list[dict], memory: list[dict[str, Any]],
                known_bad: set[tuple[str, str]],
                product_memory: dict[str, Any] | None = None) -> list[dict]:
    """Search Layer: re-rank the live-mover universe with OUTCOME MEMORY, not raw score alone. A symbol
    with a symbol-wide confirmed-bad record is penalised hard; a symbol with prior good_signal is
    nudged up. Each row carries a _priority and a human _reason (why it ranks where it does)."""
    bad_syms = {k[0] for k in known_bad if k[1] == "*"}
    good: dict[str, int] = {}
    for m in memory:
        if m.get("diagnosis") == "good_signal" or m.get("result") == "take":
            good[str(m.get("symbol"))] = good.get(str(m.get("symbol")), 0) + 1
    product_scores = _product_scores_by_symbol(product_memory)
    out = []
    for mv in movers:
        sym = str(mv.get("symbol") or (mv.get("inst_id") or "").replace("-", "_"))
        base = float(mv.get("score") or 0.0)
        penalty = 5.0 if sym in bad_syms else 0.0
        bonus = min(2.0, 0.5 * good.get(sym, 0))
        product = product_scores.get(sym) or {}
        product_score = float(product.get("score") or 0.0)
        pr = base - penalty + bonus + product_score
        out.append({**mv, "_priority": round(pr, 3),
                    "_reason": f"bucket={mv.get('_bucket')} score={base:.1f} good+{bonus:.1f} "
                               f"knownbad-{penalty:.0f} {product.get('reason') or 'product_memory=none'} "
                               f"move%={mv.get('move_pct')} vol=${(mv.get('vol_usd') or 0) / 1e6:.0f}M"})
    out.sort(key=lambda r: -r["_priority"])
    return out


def _product_cell_stats(
    product_memory: dict[str, Any] | None,
    symbol: str,
    timeframe: str,
    family: str,
) -> dict[str, Any]:
    by_cell = (product_memory or {}).get("by_cell") if isinstance(product_memory, dict) else None
    if not isinstance(by_cell, dict):
        return {}
    exact = by_cell.get(f"{symbol}|{timeframe}|{family}")
    if isinstance(exact, dict):
        return exact
    prefix = f"{symbol}|{timeframe}|{family}"
    for key, stats in by_cell.items():
        if str(key).startswith(prefix) and isinstance(stats, dict):
            return stats
    return {}


def _product_profile_cell_stats(
    product_memory: dict[str, Any] | None,
    symbol: str,
    timeframe: str,
    family: str,
    profile_id: str,
) -> dict[str, Any]:
    by_profile_cell = (
        (product_memory or {}).get("by_geometry_profile_cell")
        if isinstance(product_memory, dict)
        else None
    )
    if not isinstance(by_profile_cell, dict):
        return {}
    exact = by_profile_cell.get(f"{symbol}|{timeframe}|{family}|{profile_id}")
    return exact if isinstance(exact, dict) else {}


def _profile_is_disfavored(stats: dict[str, Any]) -> bool:
    terminal = int(stats.get("terminal_rows") or 0)
    if terminal < 3:
        return False
    wins = int(stats.get("win_rows") or 0)
    losses = int(stats.get("loss_rows") or 0)
    gave_back = int(stats.get("gave_back_rows") or 0)
    pnl = float(stats.get("paper_pnl_usdt") or 0.0)
    if losses >= wins and pnl < 0:
        return True
    return gave_back >= max(1, wins) and pnl < 0


def _product_memory_profile(stats: dict[str, Any], *, family: str) -> str:
    """Deterministic profile preference from product paper outcomes.

    Product memory is the broader, money-normalized feedback loop.  When it has
    enough terminal rows for a cell, it should outrank the older lightweight
    paper_signal_memory hints: a few historic takes must not request runner_probe
    on a cell that is now consistently losing.
    """
    terminal = int(stats.get("terminal_rows") or 0)
    if terminal < 3:
        return ""
    wins = int(stats.get("win_rows") or 0)
    losses = int(stats.get("loss_rows") or 0)
    gave_back = int(stats.get("gave_back_rows") or 0)
    pnl = float(stats.get("paper_pnl_usdt") or 0.0)
    tactical = family in {"early_tp_tactical", "reversal_fade"}
    if pnl < 0 and losses > wins:
        if gave_back >= max(1, wins):
            return "faster_capture"
        return "base" if tactical else "stop_relief"
    if pnl > 0 and wins > losses:
        if gave_back >= max(2, wins // 2):
            return "faster_capture"
        return "runner_probe"
    return ""


def _bootstrap_profile_for_family(*, family: str, timeframe: str) -> str:
    """One bounded exploration profile for cells that do not have enough memory yet.

    The farm should not wait for losses before it tries a second geometry, but
    the search still must stay cheap and deterministic.  This picks one extra
    profile per family/timeframe; prices are still computed only by family code.
    """
    tactical = family in {"early_tp_tactical", "reversal_fade"}
    if tactical:
        return "faster_capture"
    if timeframe in {"1h", "4h", "1d"}:
        return "runner_probe"
    return "stop_relief"


def geometry_profiles_for_cell(
    memory: list[dict[str, Any]],
    product_memory: dict[str, Any] | None,
    *,
    symbol: str,
    timeframe: str,
    family: str,
) -> list[str]:
    """Pick up to two bounded geometry profiles for the next farm probe.

    This is intentionally deterministic: outcome memory can request a profile
    label, but only family code computes prices. The first profile remains the
    legacy/base geometry so old behavior is still comparable.
    """
    diag: dict[str, int] = {}
    results: dict[str, int] = {}
    for row in memory:
        if (
            str(row.get("symbol") or "") != symbol
            or str(row.get("timeframe") or "") != timeframe
            or str(row.get("family") or "") != family
        ):
            continue
        d = str(row.get("diagnosis") or "")
        r = str(row.get("result") or "")
        if d:
            diag[d] = diag.get(d, 0) + 1
        if r:
            results[r] = results.get(r, 0) + 1

    stats = _product_cell_stats(product_memory, symbol, timeframe, family)
    gave_back = int(stats.get("gave_back_rows") or 0)
    wins = int(stats.get("win_rows") or 0)
    losses = int(stats.get("loss_rows") or 0)
    terminal = int(stats.get("terminal_rows") or 0)
    pnl = float(stats.get("paper_pnl_usdt") or 0.0)

    product_selected = _product_memory_profile(stats, family=family)
    terminal = int(stats.get("terminal_rows") or 0)
    selected = product_selected or "base"
    if not product_selected and diag.get("stop_too_tight", 0) >= 1:
        selected = "stop_relief"
    elif not product_selected and (
        diag.get("bad_exit_gave_back", 0) + diag.get("target_too_far", 0) >= 1
        or gave_back >= max(1, wins)
    ):
        selected = "faster_capture"
    elif not product_selected and (
        (diag.get("good_signal", 0) + results.get("take", 0) >= 2)
        or (terminal >= 3 and wins > losses and pnl > 0)
    ):
        selected = "runner_probe"
    elif not product_selected and terminal < 3:
        selected = _bootstrap_profile_for_family(family=family, timeframe=timeframe)

    if selected != "base":
        from src.research_lab.trading_policy_calibration import profile_verdict

        calibration_verdict = profile_verdict((product_memory or {}).get("calibration"), selected)
        if calibration_verdict == "demote":
            selected = "base"
        profile_stats = _product_profile_cell_stats(product_memory, symbol, timeframe, family, selected)
        if _profile_is_disfavored(profile_stats):
            selected = "base"

    return ["base"] if selected == "base" else ["base", selected]


def write_selection_snapshot(private_root: Path, ranked: list[dict], top_n: int = 20) -> Path:
    out = Path(private_root) / "state" / "derived" / "paper_selection.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, int] = {}
    for r in ranked:
        buckets[str(r.get("_bucket"))] = buckets.get(str(r.get("_bucket")), 0) + 1
    payload = {"schema": "paper_selection.v1", "total": len(ranked), "by_bucket": buckets,
               "top": [{"symbol": r.get("symbol"), "bucket": r.get("_bucket"),
                        "priority": r.get("_priority"), "reason": r.get("_reason")} for r in ranked[:top_n]],
               "disclaimer": "search-layer ranking of the live-mover universe; research-only"}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def run_cycle(private_root: Path, *, mode: str = "live", timeframes=("15m", "1h"), max_new=5,
              apply: bool = False, provider=None, now: float | None = None,
              families_arg=None, pfr_db_path: Path | None = None,
              pfr_quality_policy: dict | None = None,
              max_pfr_scan: int = 30,
              max_pfr_fetches: int | None = 12,
              pfr_reserved_new: int = 0,
              max_observe: int | None = None,
              max_live_fetches: int | None = 12,
              max_network_fetches: int | None = None,
              max_wall_seconds: float | None = None,
              should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    private_root = Path(private_root)
    started_mono = time.monotonic()
    deadline = (
        started_mono + max(0.1, float(max_wall_seconds))
        if max_wall_seconds is not None and float(max_wall_seconds) > 0
        else None
    )

    def yield_requested() -> bool:
        return bool(
            (deadline is not None and time.monotonic() >= deadline)
            or (should_stop is not None and should_stop())
        )

    if provider is None:
        from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider
        provider = OkxPublicMarketDataProvider()
    now = time.time() if now is None else now
    now_ms = int(now * 1000)
    existing = store.load_signals(private_root)
    by_key_active = {s.dedup_key for s in existing if s.status in ACTIVE}
    # PFR is the validated/main-paper lane. Broad farm watches are useful research
    # candidates, but they must not starve a validated setup with the same
    # symbol/timeframe/family identity. PFR therefore dedups against active PFR
    # signals only; any new PFR signal is then added to by_key_active so the broad
    # lane cannot create a second watch on top of it in the same cycle.
    pfr_key_active = {
        s.dedup_key
        for s in existing
        if s.status in ACTIVE and str(s.source or "") == "pfr_farm"
    }
    recent_terminal = {(s.dedup_key, s.data_fingerprint) for s in existing if s.status in TERMINAL}
    last_seen = {s.dedup_key: s.created_at for s in existing}
    last_seen_cell: dict[tuple[str, str, str], float] = {}
    for s in existing:
        cell = (str(s.symbol), str(s.timeframe), str(s.setup_family))
        last_seen_cell[cell] = max(last_seen_cell.get(cell, 0.0), float(s.created_at or 0.0))
    known_bad = lane.load_known_bad(private_root)
    mem = load_memory(private_root)
    product_memory = load_product_memory(private_root)
    learned_bad = learn_known_bad(mem)
    fam_order = families_arg or family_priority(mem)
    network_fetches = 0
    network_limit = None if max_network_fetches is None else max(0, int(max_network_fetches))

    def fetch_with_budget(symbol: str, tf: str) -> tuple[list[dict[str, Any]], bool]:
        nonlocal network_fetches
        if network_limit is not None and network_fetches >= network_limit:
            return [], False
        network_fetches += 1
        return _fetch(provider, symbol, tf, now_ms), True

    observed, closed = 0, 0
    gate_counts: dict[str, int] = {}
    active_seen = 0
    # (1) re-observe active signals on fresh bars (+ wall-clock / no-data age-out so none strand)
    for s in existing:
        if yield_requested():
            gate_counts["wall_or_stop_limit_reached"] = gate_counts.get("wall_or_stop_limit_reached", 0) + 1
            break
        if s.status not in ("armed", "opened_paper"):
            continue
        if max_observe is not None and active_seen >= max_observe:
            gate_counts["observe_scan_limit_reached"] = gate_counts.get("observe_scan_limit_reached", 0) + 1
            break
        active_seen += 1
        candles, fetch_attempted = fetch_with_budget(s.symbol, s.timeframe)
        if not fetch_attempted:
            gate_counts["observe_network_fetch_limit_reached"] = (
                gate_counts.get("observe_network_fetch_limit_reached", 0) + 1
            )
            break
        if not candles:
            s.outcome = {**(s.outcome or {}), "result": "no_data",
                         "no_data_count": int((s.outcome or {}).get("no_data_count") or 0) + 1}
            aged = lane.age_out(s, now)
            if aged:
                s = lane.review(s)
                closed += 1
            if apply:
                store.update_signal(private_root, s)
                if aged:
                    record_memory(private_root, s)
            continue
        before = s.status
        s = lane.observe(s, candles)
        if s.status not in TERMINAL:
            lane.age_out(s, now)         # not filled and past expiry -> expire instead of stranding
        s = lane.review(s)
        observed += 1
        if s.status != before and s.status in TERMINAL:
            closed += 1
            if apply:
                store.update_signal(private_root, s)
                lane.write_review_artifact(private_root, s, candles)
                record_memory(private_root, s)
        elif apply:
            store.update_signal(private_root, s)

    max_new = max(0, int(max_new))
    pfr_reserved = 0
    if pfr_db_path is not None:
        pfr_reserved = min(max_new, max(0, int(pfr_reserved_new)))
    live_new_cap = max(0, max_new - pfr_reserved)

    # (2) PFR lane first: validated/PAPER_FORWARD_READY setups feed main-paper.
    pfr_counts: dict[str, int] = {}
    pfr_gap_samples: list[dict[str, Any]] = []
    new_sigs = []
    pfr_new_keys: set[str] = set()
    if pfr_db_path is not None and max_new > 0 and not yield_requested():
        pfr_cap = max_new if pfr_reserved <= 0 else pfr_reserved
        if pfr_reserved:
            pfr_counts["pfr_reserved_slots"] = pfr_reserved
        all_pfr = pfr_bridge.load_pfr_records(pfr_db_path)
        passed_pfr, rejected_pfr = pfr_bridge.apply_quality_policy(
            all_pfr, policy=pfr_quality_policy
        )
        pfr_counts["pfr_records_loaded"] = len(all_pfr)
        pfr_counts["pfr_passed_quality"] = len(passed_pfr)
        pfr_counts["pfr_rejected_quality"] = len(rejected_pfr)
        for row in rejected_pfr:
            for reason in row.get("_rejection_reasons") or []:
                key = f"pfr_rejected_quality:{str(reason)[:50]}"
                pfr_counts[key] = pfr_counts.get(key, 0) + 1
        pfr_counts["pfr_unique_setups"] = len(
            {(r["symbol"], r["timeframe"], r["family"]) for r in passed_pfr}
        )
        pfr_gap_memory = load_pfr_gap_memory(private_root)
        if pfr_gap_memory:
            prioritized = prioritize_pfr_records_by_gap_memory(passed_pfr, pfr_gap_memory)
            pfr_counts["pfr_gap_memory_keys"] = len(pfr_gap_memory)
            pfr_counts["pfr_gap_memory_prioritized"] = sum(
                1
                for row in prioritized
                if (
                    str(row.get("symbol") or ""),
                    str(row.get("timeframe") or ""),
                    str(row.get("family") or ""),
                )
                in pfr_gap_memory
            )
            passed_pfr = prioritized

        active_setup_ids: set[str] = set()
        for s in existing:
            if s.status in ACTIVE and str(s.source or "") == "pfr_farm":
                sid = (s.validator_context or {}).get("setup_id") or ""
                if sid:
                    active_setup_ids.add(sid)

        pfr_sigs = pfr_bridge.generate_pfr_signals(
            passed_pfr,
            provider=provider,
            now=now,
            mode=mode,
            active_dedup=pfr_key_active,
            active_setup_ids=active_setup_ids,
            recent_fingerprints=recent_terminal,
            max_pfr=max(0, pfr_cap),
            timeframes=timeframes,
            status_counts=pfr_counts,
            max_pfr_scan=max_pfr_scan,
            max_pfr_fetches=max_pfr_fetches,
            gap_samples=pfr_gap_samples,
            should_stop=yield_requested,
        )
        new_sigs.extend(pfr_sigs)
        for sig, _ in pfr_sigs:
            by_key_active.add(sig.dedup_key)
            pfr_new_keys.add(sig.dedup_key)
        for k, v in pfr_counts.items():
            gate_counts[k] = gate_counts.get(k, 0) + v

    live_new_cap = max(0, max_new - len(new_sigs))

    # (3) generate new, deduplicated -- over the MEMORY-RANKED live-mover universe (search layer)
    movers = rank_movers(_load_movers(private_root), mem, known_bad, product_memory)
    if apply:
        write_selection_snapshot(private_root, movers)
    live_fetches = 0
    live_fetch_limit_reached = False
    for mv in movers:
        if yield_requested():
            gate_counts["wall_or_stop_limit_reached"] = gate_counts.get("wall_or_stop_limit_reached", 0) + 1
            break
        if len(new_sigs) >= live_new_cap or live_fetch_limit_reached:
            break
        inst = str(mv.get("inst_id") or "")
        symbol = str(mv.get("symbol") or inst.replace("-", "_"))
        for tf in timeframes:
            if yield_requested():
                gate_counts["wall_or_stop_limit_reached"] = gate_counts.get("wall_or_stop_limit_reached", 0) + 1
                live_fetch_limit_reached = True
                break
            if len(new_sigs) >= live_new_cap:
                break
            if max_live_fetches is not None and live_fetches >= max(0, int(max_live_fetches)):
                gate_counts["live_fetch_limit_reached"] = gate_counts.get("live_fetch_limit_reached", 0) + 1
                live_fetch_limit_reached = True
                break
            candles, fetch_attempted = fetch_with_budget(symbol, tf)
            if not fetch_attempted:
                gate_counts["network_fetch_limit_reached"] = gate_counts.get("network_fetch_limit_reached", 0) + 1
                live_fetch_limit_reached = True
                break
            live_fetches += 1
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
            geometry_profiles = {
                fam: geometry_profiles_for_cell(
                    mem,
                    product_memory,
                    symbol=symbol,
                    timeframe=tf,
                    family=fam,
                )
                for fam in fam_order
            }
            for sig, fam in families.generate(symbol, inst, tf, decide, mover=mv, now=now,
                                              boundary_ts=boundary_ts, mode=mode, families=fam_order,
                                              geometry_profiles=geometry_profiles):
                if len(new_sigs) >= live_new_cap:
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
                profile_id = str((sig.validator_context or {}).get("geometry_profile_id") or "base")
                if profile_id != "base":
                    cell = (str(sig.symbol), str(sig.timeframe), str(sig.setup_family))
                    if now - last_seen_cell.get(cell, 0.0) < REGEN_TTL_SECONDS:
                        gate_counts["geometry_profile_cell_ttl"] = (
                            gate_counts.get("geometry_profile_cell_ttl", 0) + 1
                        )
                        continue
                gate_counts[f"family:{fam}"] = gate_counts.get(f"family:{fam}", 0) + 1
                gate_counts[f"geometry_profile:{profile_id}"] = (
                    gate_counts.get(f"geometry_profile:{profile_id}", 0) + 1
                )
                if mode == "replay":
                    sig = lane.review(lane.observe(sig, candles))
                new_sigs.append((sig, candles))
                by_key_active.add(sig.dedup_key)

    if apply:
        if pfr_new_keys:
            for old in existing:
                if old.status not in ACTIVE or old.source == "pfr_farm" or old.dedup_key not in pfr_new_keys:
                    continue
                old.status = "invalidated"
                old.outcome = {
                    **(old.outcome or {}),
                    "result": "superseded_by_pfr",
                    "superseded_by": "validated_pfr_signal",
                }
                old.review = {
                    **(old.review or {}),
                    "diagnosis": "superseded_by_pfr",
                    "note": "A validated PFR signal replaced this broad farm-watch card.",
                }
                store.update_signal(private_root, old)
                gate_counts["superseded_by_pfr"] = gate_counts.get("superseded_by_pfr", 0) + 1
        for sig, candles in new_sigs:
            sig = _attach_lineage(private_root, sig, candles, mode=mode, provider=provider)
            store.append_signal(private_root, sig)
            sig.chart_context_ref = str(lane.write_review_artifact(private_root, sig, candles))
            store.update_signal(private_root, sig)   # persist the chart ref (armed cards get a chart too)
            if sig.status in TERMINAL:
                record_memory(private_root, sig)
        store.write_state_snapshot(private_root)
        if pfr_gap_samples:
            _append_pfr_gap_telemetry(private_root, pfr_gap_samples, pfr_counts, now)

    report = {"mode": mode, "apply": apply, "observed": observed, "closed": closed,
              "generated": len(new_sigs), "gate_counts": gate_counts,
              "live_fetches": live_fetches,
              "max_live_fetches": max_live_fetches,
              "network_fetches": network_fetches,
              "max_network_fetches": max_network_fetches,
              "elapsed_seconds": round(time.monotonic() - started_mono, 3),
              "max_wall_seconds": max_wall_seconds,
              "yield_requested": yield_requested(),
              "pipeline_counts": _pipeline_counts(gate_counts, generated=len(new_sigs), observed=observed),
              "resource_caps": default_caps().to_dict(),
              "pfr_counts": pfr_counts,
              "pfr_gap_samples": len(pfr_gap_samples),
              "max_pfr_fetches": max_pfr_fetches,
              "state": store.current_state_view(private_root)["by_status"] if apply else {},
              "new_cards": [s.signal_id for s, _ in new_sigs]}
    if apply:
        _write_status(private_root, report, now)
    return report


def _append_pfr_gap_telemetry(
    private_root: Path,
    samples: list[dict[str, Any]],
    pfr_counts: dict[str, int],
    now: float,
) -> Path:
    selected = sum(1 for sample in samples if str(sample.get("selection_state") or "").endswith("_selected"))
    payload = {
        "schema": "PFRGapTelemetryCycle.v1",
        "updated_at": round(now, 1),
        "summary": {
            "samples": len(samples),
            "selected": selected,
            "counts": dict(pfr_counts),
        },
        "samples": samples,
        "all_research_only": True,
        "disclaimer": "private calibration telemetry; PFR watch only; NOT orders",
    }
    path = _pfr_gap_telemetry_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _pipeline_counts(gate_counts: dict[str, int], *, generated: int, observed: int) -> dict[str, Any]:
    counts = new_stage_counts()
    counts["processed"] = int(generated) + int(observed)
    for reason, n in gate_counts.items():
        for _ in range(int(n or 0)):
            add_reason(counts, reason)
    return counts


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
    # Only genuine SETUP/direction failures count as "bad" - these are diagnoses review() actually emits.
    # bad_exit_gave_back is an EXIT problem (fixed by execution geometry, not a dead setup); missed_pullback
    # is a missed WIN (price ran the right way) - neither marks the setup known-bad.
    bad_diag = {"wrong_direction", "no_follow_through", "valid_loss"}
    for m in memory:
        key = (m.get("symbol"), m.get("timeframe"), m.get("family"))
        if None in key:
            continue
        is_bad = (m.get("diagnosis") in bad_diag) or (
            m.get("result") == "stop" and m.get("diagnosis") not in ("stop_too_tight", "bad_exit_gave_back"))
        agg.setdefault(key, []).append("bad" if is_bad else "good")
    out = set()
    for key, tags in agg.items():
        if len(tags) >= min_n and all(t == "bad" for t in tags):
            out.add(key)
    return out


def family_priority(memory: list[dict[str, Any]], *, default=None) -> list[str]:
    """Order families by their good-signal rate in memory (learning influences the NEXT cycle's order).
    Unseen families keep default order after the proven ones. Deterministic; never mints a signal."""
    default = default or list(families.FAMILIES)
    score = {f: [0, 0] for f in default}
    for m in memory:
        f = m.get("family")
        if f in score:
            score[f][1] += 1
            if m.get("diagnosis") == "good_signal" or m.get("result") == "take":
                score[f][0] += 1

    def rate(f):
        return (score[f][0] / score[f][1]) if score[f][1] else -1.0
    return sorted(default, key=lambda f: -rate(f))


# Allowed keys for an LLM REVIEWER's advice: annotation only. It can never carry signal geometry
# (entry/stop/side/take/order) - deterministic code remains the sole authority that mints a signal.
_ADVICE_ALLOWED = {"diagnosis_note", "confidence", "suggested_family_priority", "rationale"}
_ADVICE_FORBIDDEN = {"entry_zone", "stop_loss", "take_profit_plan", "side", "signal_id", "order", "size"}


def validate_advice(advice: dict[str, Any]) -> tuple[bool, list[str]]:
    """Schema gate for LLM advisory: reject anything that tries to mint/alter a signal. Advisory is
    metadata only; the deterministic lane is the authority. Returns (ok, problems)."""
    problems = []
    for k in advice:
        if k in _ADVICE_FORBIDDEN:
            problems.append(f"advice may not set signal field {k!r}")
        elif k not in _ADVICE_ALLOWED:
            problems.append(f"unknown advice field {k!r}")
    conf = advice.get("confidence")
    if conf is not None and not (isinstance(conf, (int, float)) and 0 <= conf <= 1):
        problems.append("confidence must be in [0,1]")
    return (not problems), problems


def _active_lock(lock_path: Path, sleep_seconds: int) -> bool:
    if not lock_path.exists():
        return False
    age = time.time() - lock_path.stat().st_mtime
    return age < 2 * max(60, sleep_seconds)


def run_loop(private_root: Path, *, cycles: int, sleep_seconds: int = 0, stop_file: str = "",
             mode: str = "live", timeframes=("15m", "1h"), max_new=5, provider=None,
             apply: bool = True, lock_file: str | Path | None = None,
             pfr_db_path: Path | None = None, pfr_quality_policy: dict | None = None,
             max_pfr_scan: int = 30,
             pfr_reserved_new: int = 0,
             max_observe: int | None = None) -> list[dict]:
    private_root = Path(private_root)
    lock_path = Path(lock_file) if lock_file else private_root / "state" / "paper_signals_loop.lock"
    if _active_lock(lock_path, sleep_seconds):
        age = time.time() - lock_path.stat().st_mtime
        raise RuntimeError(
            f"another paper_signals loop appears active (lock {lock_path}, age {age:.0f}s); "
            "stop it or delete the stale lock to override"
        )
    reports = []
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(str(time.time()), encoding="utf-8")
        for i in range(max(1, cycles)):
            if stop_file and Path(stop_file).exists():
                reports.append({"stopped": True, "at_cycle": i})
                break
            reports.append(run_cycle(private_root, mode=mode, timeframes=timeframes, max_new=max_new,
                                     apply=apply, provider=provider,
                                     pfr_db_path=pfr_db_path,
                                     pfr_quality_policy=pfr_quality_policy,
                                     max_pfr_scan=max_pfr_scan,
                                     pfr_reserved_new=pfr_reserved_new,
                                     max_observe=max_observe))
            lock_path.write_text(str(time.time()), encoding="utf-8")
            if sleep_seconds and i < cycles - 1:
                time.sleep(max(1, sleep_seconds))
        return reports
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass
