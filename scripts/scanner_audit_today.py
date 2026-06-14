# -*- coding: utf-8 -*-
"""scanner_audit_today.py — read-only audit of today's scanner activity.

Usage:
    python scripts/scanner_audit_today.py           # today's audit
    python scripts/scanner_audit_today.py --json    # JSON output

Shows: events ingested, cards created, GO/NO_GO/WATCH breakdown,
Telegram delivery details, source quality, price measurement status.
No secrets, no absolute paths, no write operations.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_JOURNAL = _ROOT / "logs" / "scout" / "scanner_journal.jsonl"
_DELIVERY = _ROOT / "logs" / "scout" / "telegram_delivery.jsonl"
_DROPS = _ROOT / "logs" / "scout" / "drops.jsonl"
_ROUTING = _ROOT / "logs" / "scout" / "routing_audit.jsonl"
_BUDGET = _ROOT / "logs" / "scout" / "llm_budget.jsonl"
_INGEST = _ROOT / "logs" / "scout" / "ingest_log.jsonl"
_EVENTS = _ROOT / "logs" / "scout" / "scanner_events.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _is_today(ts: str | None) -> bool:
    if not ts:
        return False
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return t.date() == _today_utc()
    except Exception:
        return False


def build_audit() -> dict:
    """Build today's audit dict."""
    today = _today_utc()
    journal = _read_jsonl(_JOURNAL)
    delivery = _read_jsonl(_DELIVERY)
    drops = _read_jsonl(_DROPS)
    routing = _read_jsonl(_ROUTING)
    budget = _read_jsonl(_BUDGET)
    ingest = _read_jsonl(_INGEST)
    events = _read_jsonl(_EVENTS)

    # Filter to today
    today_journal = [r for r in journal if _is_today(r.get("ts_utc"))]
    today_delivery = [r for r in delivery if _is_today(r.get("ts"))]
    today_drops = [r for r in drops if _is_today(r.get("ts"))]
    today_routing = [r for r in routing if _is_today(r.get("ts"))]
    today_budget = [r for r in budget if _is_today(r.get("ts"))]
    today_ingest = [r for r in ingest if _is_today(r.get("ts"))]

    # Ingest counts
    ingest_by_source = Counter()
    for r in today_ingest:
        ingest_by_source[r.get("source", "?")] += 1

    # Routing drops
    routing_by_reason = Counter()
    routing_by_source = Counter()
    for r in today_routing:
        s = r.get("skipped")
        if s:
            routing_by_reason[s] += 1
        routing_by_source[r.get("source", "?")] += 1

    # Journal cards
    verdict_counts = Counter()
    source_counts = Counter()
    gate_counts = Counter()
    price_reasons = Counter()
    for r in today_journal:
        verdict_counts[r.get("verdict", "?")] += 1
        source_counts[r.get("source", "?")] += 1
        gate_counts[r.get("escalation_gate", "?")] += 1
        pr = r.get("price_reason")
        if pr:
            price_reasons[pr] += 1
        elif r.get("price_at_decision") is not None:
            price_reasons["supported"] += 1
        else:
            price_reasons["unknown"] += 1

    # Delivery details
    delivery_status = Counter()
    delivery_reason = Counter()
    for r in today_delivery:
        delivery_status[r.get("status", "?")] += 1
        delivery_reason[r.get("reason", "?")] += 1

    # Source quality (from events)
    today_events = [r for r in events if _is_today(r.get("recorded_at"))]
    source_extraction = defaultdict(lambda: {"ok": 0, "partial": 0, "title_only": 0, "total": 0})
    for r in today_events:
        src = r.get("source", {}).get("source_id", "?")
        method = r.get("extraction", {}).get("method", "unknown")
        status = r.get("extraction", {}).get("status", "unknown")
        source_extraction[src]["total"] += 1
        if method == "title_only":
            source_extraction[src]["title_only"] += 1
        elif status == "ok":
            source_extraction[src]["ok"] += 1
        elif status == "partial":
            source_extraction[src]["partial"] += 1

    # Identity null rate
    identity_fields = ["asset_class", "trigger_role", "channel_kind",
                       "context_found", "identity_reason"]
    identity_null = {f: 0 for f in identity_fields}
    for r in today_events:
        ident = r.get("identity", {})
        for f in identity_fields:
            if ident.get(f) is None:
                identity_null[f] += 1

    # LLM budget today
    total_tokens = sum(b.get("total_tokens", 0) for b in today_budget)
    total_cost = sum(b.get("cost_rub", 0) for b in today_budget)
    total_cards = sum(b.get("n_cards", 0) for b in today_budget)

    return {
        "date": str(today),
        "ingested": {
            "total": len(today_ingest),
            "by_source": dict(ingest_by_source.most_common()),
        },
        "routing_drops": {
            "total": len(today_routing),
            "drop_log_total": len(today_drops),
            "by_reason": dict(routing_by_reason.most_common()),
            "by_source": dict(routing_by_source.most_common(10)),
        },
        "cards": {
            "total": len(today_journal),
            "by_verdict": dict(verdict_counts.most_common()),
            "by_source": dict(source_counts.most_common(10)),
            "by_gate": dict(gate_counts.most_common()),
        },
        "delivery": {
            "total": len(today_delivery),
            "by_status": dict(delivery_status.most_common()),
            "by_reason": dict(delivery_reason.most_common()),
        },
        "price_measurement": dict(price_reasons.most_common()),
        "source_quality": {
            src: dict(q) for src, q in sorted(source_extraction.items(), key=lambda x: -x[1]["total"])
        },
        "identity_null_rate": {f: f"{v}/{len(today_events)}" if today_events else "0/0"
                               for f, v in identity_null.items()},
        "llm_today": {
            "passes": len(today_budget),
            "tokens": total_tokens,
            "cost_rub": round(total_cost, 2),
            "cards": total_cards,
        },
    }


def format_audit(audit: dict) -> str:
    """Format audit dict as human-readable text."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"  SCANNER AUDIT — {audit.get('date', '?')}")
    lines.append("=" * 60)
    lines.append("")

    ingested = audit.get("ingested", {})
    lines.append(f"  Events ingested: {ingested.get('total', 0)}")
    by_src = ingested.get("by_source", {})
    if by_src:
        for s, c in sorted(by_src.items(), key=lambda x: -x[1]):
            lines.append(f"    {s}: {c}")
    lines.append("")

    routing = audit.get("routing_drops", {})
    lines.append(f"  Routing decisions: {routing.get('total', 0)}")
    lines.append(f"  Drop log rows:     {routing.get('drop_log_total', 0)}")
    lines.append(f"  Drop log rows:     {routing.get('drop_log_total', 0)}")
    by_reason = routing.get("by_reason", {})
    if by_reason:
        lines.append("  By skip reason:")
        for r, c in sorted(by_reason.items(), key=lambda x: -x[1]):
            lines.append(f"    {r}: {c}")
    lines.append("")

    cards = audit.get("cards", {})
    lines.append(f"  Cards created: {cards.get('total', 0)}")
    by_verdict = cards.get("by_verdict", {})
    if by_verdict:
        lines.append("  By verdict:")
        for v, c in sorted(by_verdict.items(), key=lambda x: -x[1]):
            lines.append(f"    {v}: {c}")
    by_gate = cards.get("by_gate", {})
    if by_gate:
        lines.append("  By gate:")
        for g, c in sorted(by_gate.items(), key=lambda x: -x[1]):
            lines.append(f"    {g}: {c}")
    lines.append("")

    delivery = audit.get("delivery", {})
    lines.append(f"  Telegram delivery: {delivery.get('total', 0)} attempts")
    by_status = delivery.get("by_status", {})
    if by_status:
        for s, c in sorted(by_status.items(), key=lambda x: -x[1]):
            lines.append(f"    {s}: {c}")
    by_reason = delivery.get("by_reason", {})
    if by_reason:
        lines.append("  Reasons:")
        for r, c in sorted(by_reason.items(), key=lambda x: -x[1]):
            lines.append(f"    {r}: {c}")
    lines.append("")

    prices = audit.get("price_measurement", {})
    if prices:
        lines.append("  Price measurement:")
        for r, c in sorted(prices.items(), key=lambda x: -x[1]):
            lines.append(f"    {r}: {c}")
        lines.append("")

    sq = audit.get("source_quality", {})
    if sq:
        lines.append("  Source extraction quality:")
        for src, q in sq.items():
            total = q.get("total", 0)
            ok = q.get("ok", 0)
            partial = q.get("partial", 0)
            title_only = q.get("title_only", 0)
            pct = f"{ok/total*100:.0f}% ok" if total else "n/a"
            lines.append(f"    {src}: {total} docs, {pct}, {partial} partial, {title_only} title_only")
        lines.append("")

    identity = audit.get("identity_null_rate", {})
    if any(v != "0/0" for v in identity.values()):
        lines.append("  Identity null rate:")
        for f, rate in identity.items():
            lines.append(f"    {f}: {rate}")
        lines.append("")

    llm = audit.get("llm_today", {})
    if llm:
        lines.append("  LLM today:")
        lines.append(f"    Passes: {llm.get('passes', 0)}")
        lines.append(f"    Tokens: {llm.get('tokens', 0):,}")
        lines.append(f"    Cost: {llm.get('cost_rub', 0):.2f} RUB")
        lines.append(f"    Cards: {llm.get('cards', 0)}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Scanner audit today")
    ap.add_argument("--json", action="store_true", help="output as JSON")
    args = ap.parse_args()

    audit = build_audit()
    if args.json:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
    else:
        print(format_audit(audit))


if __name__ == "__main__":
    main()
