# -*- coding: utf-8 -*-
"""scanner_status.py — read-only scanner status overview.

Usage:
    python scripts/scanner_status.py          # quick status
    python scripts/scanner_status.py --json   # JSON output

Shows: scanner alive, last run, source counts, cards, Telegram delivery,
top skip reasons, price status, identity null rate.
No secrets, no absolute paths, no write operations.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_JOURNAL = _ROOT / "logs" / "scout" / "scanner_journal.jsonl"
_DELIVERY = _ROOT / "logs" / "scout" / "telegram_delivery.jsonl"
_DROPS = _ROOT / "logs" / "scout" / "drops.jsonl"
_ROUTING = _ROOT / "logs" / "scout" / "routing_audit.jsonl"
_BUDGET = _ROOT / "logs" / "scout" / "llm_budget.jsonl"
_EVENTS = _ROOT / "logs" / "scout" / "scanner_events.jsonl"


def _read_jsonl(path: Path, limit: int = 500) -> list[dict]:
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
    return rows[-limit:]


def _parse_ts(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_scanner_alive() -> bool:
    """Check if scanner_v0 or news_scanner_loop process is running."""
    try:
        import subprocess
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
                    "ForEach-Object { $_.CommandLine }"
                ),
            ],
            capture_output=True, text=True, timeout=5,
        )
        out = result.stdout.lower()
        return "scanner_v0.py" in out or "news_scanner_loop" in out
    except Exception:
        return False


def build_status() -> dict:
    """Build scanner status dict."""
    journal = _read_jsonl(_JOURNAL, limit=2000)
    delivery = _read_jsonl(_DELIVERY, limit=200)
    drops = _read_jsonl(_DROPS, limit=1000)
    budget = _read_jsonl(_BUDGET, limit=100)
    events = _read_jsonl(_EVENTS, limit=200)

    # Last run time
    last_run = None
    if budget:
        last_run = budget[-1].get("ts")

    # Source counts
    source_counts = Counter()
    for row in journal:
        source_counts[row.get("source", "?")] += 1

    # Verdict counts (all time)
    verdict_counts = Counter()
    for row in journal:
        verdict_counts[row.get("verdict", "?")] += 1

    # Delivery stats
    delivery_status = Counter()
    delivery_reason = Counter()
    for row in delivery:
        delivery_status[row.get("status", "?")] += 1
        delivery_reason[row.get("reason", "?")] += 1

    # Top drop reasons
    drop_reasons = Counter()
    for row in drops:
        drop_reasons[row.get("drop_reason", "?")] += 1

    # Identity null rate (from events)
    identity_fields = ["asset_class", "trigger_role", "channel_kind",
                       "context_found", "identity_reason", "identity_confidence"]
    identity_null = {f: 0 for f in identity_fields}
    for row in events:
        ident = row.get("identity", {})
        for f in identity_fields:
            if ident.get(f) is None:
                identity_null[f] += 1
    identity_null_rate = {f: f"{v}/{len(events)}" if events else "0/0"
                          for f, v in identity_null.items()}

    # Price stats (from journal)
    price_reasons = Counter()
    for row in journal:
        pr = row.get("price_reason")
        if pr:
            price_reasons[pr] += 1
        elif row.get("price_at_decision") is not None:
            price_reasons["supported"] += 1
        else:
            price_reasons["unknown"] += 1

    # LLM budget summary
    total_tokens = sum(b.get("total_tokens", 0) for b in budget)
    total_cost = sum(b.get("cost_rub", 0) for b in budget)
    total_cards = sum(b.get("n_cards", 0) for b in budget)

    return {
        "scanner_alive": _is_scanner_alive(),
        "last_run_utc": last_run,
        "total_journal_entries": len(journal),
        "total_events": len(events),
        "total_drops": len(drops),
        "verdicts": dict(verdict_counts.most_common()),
        "sources_top10": dict(source_counts.most_common(10)),
        "delivery": {
            "total": len(delivery),
            "status": dict(delivery_status.most_common()),
            "reasons": dict(delivery_reason.most_common()),
        },
        "top_drop_reasons": dict(drop_reasons.most_common(10)),
        "price_reasons": dict(price_reasons.most_common()),
        "identity_null_rate": identity_null_rate,
        "llm": {
            "total_passes": len(budget),
            "total_tokens": total_tokens,
            "total_cost_rub": round(total_cost, 2),
            "total_cards": total_cards,
        },
    }


def format_status(status: dict) -> str:
    """Format status dict as human-readable text."""
    lines = []
    lines.append("=" * 60)
    lines.append("  SCANNER STATUS")
    lines.append("=" * 60)
    lines.append("")

    alive = status.get("scanner_alive")
    lines.append(f"  Scanner alive:  {'YES' if alive else 'UNKNOWN (process check failed)'}")
    lines.append(f"  Last run:       {status.get('last_run_utc') or 'never'}")
    lines.append("")

    lines.append("  --- Data Volume ---")
    lines.append(f"  Journal entries:  {status.get('total_journal_entries', 0)}")
    lines.append(f"  Event blocks:     {status.get('total_events', 0)}")
    lines.append(f"  Drops:            {status.get('total_drops', 0)}")
    lines.append("")

    verdicts = status.get("verdicts", {})
    if verdicts:
        lines.append("  --- Verdicts (all time) ---")
        for v, count in sorted(verdicts.items(), key=lambda x: -x[1]):
            lines.append(f"    {v}: {count}")
        lines.append("")

    sources = status.get("sources_top10", {})
    if sources:
        lines.append("  --- Top Sources ---")
        for s, count in sorted(sources.items(), key=lambda x: -x[1]):
            lines.append(f"    {s}: {count}")
        lines.append("")

    delivery = status.get("delivery", {})
    dl_status = delivery.get("status", {})
    if dl_status:
        lines.append("  --- Telegram Delivery ---")
        lines.append(f"    Total attempts: {delivery.get('total', 0)}")
        for s, count in sorted(dl_status.items(), key=lambda x: -x[1]):
            lines.append(f"    {s}: {count}")
        dl_reasons = delivery.get("reasons", {})
        if dl_reasons:
            lines.append("    Reasons:")
            for r, count in sorted(dl_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"      {r}: {count}")
        lines.append("")

    drops = status.get("top_drop_reasons", {})
    if drops:
        lines.append("  --- Top Drop Reasons ---")
        for r, count in sorted(drops.items(), key=lambda x: -x[1]):
            lines.append(f"    {r}: {count}")
        lines.append("")

    prices = status.get("price_reasons", {})
    if prices:
        lines.append("  --- Price Availability ---")
        for r, count in sorted(prices.items(), key=lambda x: -x[1]):
            lines.append(f"    {r}: {count}")
        lines.append("")

    identity = status.get("identity_null_rate", {})
    if identity:
        lines.append("  --- Identity Null Rate ---")
        for f, rate in identity.items():
            lines.append(f"    {f}: {rate}")
        lines.append("")

    llm = status.get("llm", {})
    if llm:
        lines.append("  --- LLM Budget ---")
        lines.append(f"    Passes:     {llm.get('total_passes', 0)}")
        lines.append(f"    Tokens:     {llm.get('total_tokens', 0):,}")
        lines.append(f"    Cost:       {llm.get('total_cost_rub', 0):.2f} RUB")
        lines.append(f"    Cards:      {llm.get('total_cards', 0)}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Scanner status overview")
    ap.add_argument("--json", action="store_true", help="output as JSON")
    args = ap.parse_args()

    status = build_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print(format_status(status))


if __name__ == "__main__":
    main()
