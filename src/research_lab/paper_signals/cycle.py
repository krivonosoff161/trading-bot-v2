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

from src.research_lab.paper_signals import families, lane, pfr_bridge, store
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
    for grp, lst in (data.get("detail") or {}).items():
        for r in lst or []:
            rows.append({**r, "_bucket": r.get("group") or grp})
    rows.sort(key=lambda r: -(r.get("score") or 0))
    return rows


def rank_movers(movers: list[dict], memory: list[dict[str, Any]],
                known_bad: set[tuple[str, str]]) -> list[dict]:
    """Search Layer: re-rank the live-mover universe with OUTCOME MEMORY, not raw score alone. A symbol
    with a symbol-wide confirmed-bad record is penalised hard; a symbol with prior good_signal is
    nudged up. Each row carries a _priority and a human _reason (why it ranks where it does)."""
    bad_syms = {k[0] for k in known_bad if k[1] == "*"}
    good: dict[str, int] = {}
    for m in memory:
        if m.get("diagnosis") == "good_signal" or m.get("result") == "take":
            good[str(m.get("symbol"))] = good.get(str(m.get("symbol")), 0) + 1
    out = []
    for mv in movers:
        sym = str(mv.get("symbol") or (mv.get("inst_id") or "").replace("-", "_"))
        base = float(mv.get("score") or 0.0)
        penalty = 5.0 if sym in bad_syms else 0.0
        bonus = min(2.0, 0.5 * good.get(sym, 0))
        pr = base - penalty + bonus
        out.append({**mv, "_priority": round(pr, 3),
                    "_reason": f"bucket={mv.get('_bucket')} score={base:.1f} good+{bonus:.1f} "
                               f"knownbad-{penalty:.0f} move%={mv.get('move_pct')} vol=${(mv.get('vol_usd') or 0) / 1e6:.0f}M"})
    out.sort(key=lambda r: -r["_priority"])
    return out


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
              max_observe: int | None = None) -> dict[str, Any]:
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
    mem = load_memory(private_root)
    learned_bad = learn_known_bad(mem)
    fam_order = families_arg or family_priority(mem)

    observed, closed = 0, 0
    gate_counts: dict[str, int] = {}
    active_seen = 0
    # (1) re-observe active signals on fresh bars (+ wall-clock / no-data age-out so none strand)
    for s in existing:
        if s.status not in ("armed", "opened_paper"):
            continue
        if max_observe is not None and active_seen >= max_observe:
            gate_counts["observe_scan_limit_reached"] = gate_counts.get("observe_scan_limit_reached", 0) + 1
            break
        active_seen += 1
        candles = _fetch(provider, s.symbol, s.timeframe, now_ms)
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

    # (2) generate new, deduplicated -- over the MEMORY-RANKED live-mover universe (search layer)
    movers = rank_movers(_load_movers(private_root), mem, known_bad)
    if apply:
        write_selection_snapshot(private_root, movers)
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
                                              boundary_ts=boundary_ts, mode=mode, families=fam_order):
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

    # (3) PFR lane — bounded separate source, runs after movers so dedup is shared
    pfr_counts: dict[str, int] = {}
    if pfr_db_path is not None and len(new_sigs) < max_new:
        all_pfr = pfr_bridge.load_pfr_records(pfr_db_path)
        passed_pfr, rejected_pfr = pfr_bridge.apply_quality_policy(
            all_pfr, policy=pfr_quality_policy
        )
        pfr_counts["pfr_records_loaded"] = len(all_pfr)
        pfr_counts["pfr_passed_quality"] = len(passed_pfr)
        pfr_counts["pfr_rejected_quality"] = len(rejected_pfr)
        pfr_counts["pfr_unique_setups"] = len(
            {(r["symbol"], r["timeframe"], r["family"]) for r in passed_pfr}
        )

        # Collect setup_ids already active so we don't generate a duplicate for the same setup
        active_setup_ids: set[str] = set()
        for s in existing:
            if s.status in ACTIVE:
                sid = (s.validator_context or {}).get("setup_id") or ""
                if sid:
                    active_setup_ids.add(sid)
        for s, _ in new_sigs:
            sid = (s.validator_context or {}).get("setup_id") or ""
            if sid:
                active_setup_ids.add(sid)

        pfr_sigs = pfr_bridge.generate_pfr_signals(
            passed_pfr,
            provider=provider,
            now=now,
            mode=mode,
            active_dedup=by_key_active,          # shared mutable set — movers dedup carries over
            active_setup_ids=active_setup_ids,
            recent_fingerprints=recent_terminal,
            max_pfr=max(0, max_new - len(new_sigs)),
            timeframes=timeframes,
            status_counts=pfr_counts,
            max_pfr_scan=max_pfr_scan,
        )
        new_sigs.extend(pfr_sigs)
        # gate_counts merged for single report surface
        for k, v in pfr_counts.items():
            gate_counts[k] = gate_counts.get(k, 0) + v

    if apply:
        for sig, candles in new_sigs:
            store.append_signal(private_root, sig)
            sig.chart_context_ref = str(lane.write_review_artifact(private_root, sig, candles))
            store.update_signal(private_root, sig)   # persist the chart ref (armed cards get a chart too)
            if sig.status in TERMINAL:
                record_memory(private_root, sig)
        store.write_state_snapshot(private_root)

    report = {"mode": mode, "apply": apply, "observed": observed, "closed": closed,
              "generated": len(new_sigs), "gate_counts": gate_counts,
              "pfr_counts": pfr_counts,
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
    # Only genuine SETUP/direction failures count as "bad" — these are diagnoses review() actually emits.
    # bad_exit_gave_back is an EXIT problem (fixed by execution geometry, not a dead setup); missed_pullback
    # is a missed WIN (price ran the right way) — neither marks the setup known-bad.
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
# (entry/stop/side/take/order) — deterministic code remains the sole authority that mints a signal.
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
