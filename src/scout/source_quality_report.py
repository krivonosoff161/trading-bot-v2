# -*- coding: utf-8 -*-
"""
source_quality_report.py - summarize scanner source quality and routing quality.

Reads append-only scanner logs and produces per-source metrics:
  - ingest volume
  - drop reasons
  - cards / chief / low-confidence
  - phase disagreement (source prior vs headline vs final)

This is intentionally offline and read-only.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = _ROOT / "logs" / "scout"
INGEST = LOG_DIR / "ingest_log.jsonl"
DROPS = LOG_DIR / "drops.jsonl"
JOURNAL = LOG_DIR / "scanner_journal.jsonl"
ROUTING = LOG_DIR / "routing_audit.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _phase_key(value: Any) -> str:
    s = str(value or "").strip().upper()
    return s or "UNKNOWN"


def _infer_source(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "").strip()
    if source:
        return source
    url = str(row.get("source_url") or row.get("url") or "").strip()
    host = urlsplit(url).netloc.lower()
    if "cointelegraph.com" in host:
        return "cointelegraph"
    if "decrypt.co" in host:
        return "decrypt"
    if "news.google.com" in host:
        return "google_news"
    if "oilprice.com" in host:
        return "oilprice"
    if "okx.com" in host:
        return "okx_listings"
    return "unknown"


def summarize(
    *,
    ingest_rows: list[dict[str, Any]] | None = None,
    drop_rows: list[dict[str, Any]] | None = None,
    journal_rows: list[dict[str, Any]] | None = None,
    routing_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ingest_rows = ingest_rows if ingest_rows is not None else _load_jsonl(INGEST)
    drop_rows = drop_rows if drop_rows is not None else _load_jsonl(DROPS)
    journal_rows = journal_rows if journal_rows is not None else _load_jsonl(JOURNAL)
    routing_rows = routing_rows if routing_rows is not None else _load_jsonl(ROUTING)

    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "ingested": 0,
            "drops": Counter(),
            "cards": 0,
            "verdicts": Counter(),
            "phases": Counter(),
            "lead_classes": Counter(),
            "low_confidence": 0,
            "chief_called": 0,
            "routing_attempts": 0,
            "routing_skips": Counter(),
            "phase_prior_vs_headline": Counter(),
            "headline_vs_final": Counter(),
            "layer_hits": Counter(),
        }
    )

    for row in ingest_rows:
        source = _infer_source(row)
        stats[source]["ingested"] += 1

    for row in drop_rows:
        source = _infer_source(row)
        stats[source]["drops"][str(row.get("drop_reason") or "unknown")] += 1

    for row in journal_rows:
        source = _infer_source(row)
        entry = stats[source]
        entry["cards"] += 1
        entry["verdicts"][str(row.get("verdict") or "UNKNOWN")] += 1
        entry["phases"][_phase_key(row.get("event_phase"))] += 1
        entry["lead_classes"][str(row.get("lead_class") or "UNKNOWN")] += 1
        entry["layer_hits"][str(row.get("layer") or "?")] += 1
        if row.get("low_confidence"):
            entry["low_confidence"] += 1
        if row.get("chief_called"):
            entry["chief_called"] += 1

    for row in routing_rows:
        source = _infer_source(row)
        entry = stats[source]
        entry["routing_attempts"] += 1
        if row.get("layer") is not None:
            entry["layer_hits"][str(row.get("layer"))] += 1
        if row.get("skipped"):
            entry["routing_skips"][str(row.get("skipped"))] += 1

        prior = _phase_key(row.get("source_phase_prior"))
        headline = _phase_key(row.get("headline_phase"))
        final = _phase_key(row.get("final_phase"))
        if prior not in ("UNKNOWN", "MIXED") and headline not in ("UNKNOWN", "AMBIGUOUS"):
            entry["phase_prior_vs_headline"][f"{prior}->{headline}"] += 1
        if headline not in ("UNKNOWN", "AMBIGUOUS") and final not in ("UNKNOWN", "AMBIGUOUS"):
            entry["headline_vs_final"][f"{headline}->{final}"] += 1

    sources: dict[str, Any] = {}
    for source, entry in stats.items():
        cards = int(entry["cards"])
        chief_called = int(entry["chief_called"])
        sources[source] = {
            "ingested": int(entry["ingested"]),
            "cards": cards,
            "card_rate": round(cards / entry["ingested"], 4) if entry["ingested"] else 0.0,
            "chief_called": chief_called,
            "chief_rate": round(chief_called / cards, 4) if cards else 0.0,
            "low_confidence": int(entry["low_confidence"]),
            "low_confidence_rate": round(entry["low_confidence"] / cards, 4) if cards else 0.0,
            "verdicts": dict(entry["verdicts"]),
            "phases": dict(entry["phases"]),
            "lead_classes": dict(entry["lead_classes"]),
            "drops": dict(entry["drops"]),
            "routing_attempts": int(entry["routing_attempts"]),
            "routing_skips": dict(entry["routing_skips"]),
            "phase_prior_vs_headline": dict(entry["phase_prior_vs_headline"]),
            "headline_vs_final": dict(entry["headline_vs_final"]),
            "layer_hits": dict(entry["layer_hits"]),
        }

    totals = {
        "sources": len(sources),
        "ingested": sum(v["ingested"] for v in sources.values()),
        "cards": sum(v["cards"] for v in sources.values()),
        "chief_called": sum(v["chief_called"] for v in sources.values()),
        "routing_attempts": sum(v["routing_attempts"] for v in sources.values()),
    }
    return {"totals": totals, "sources": dict(sorted(sources.items()))}


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Sources: {report['totals']['sources']}",
        f"Ingested: {report['totals']['ingested']}  Cards: {report['totals']['cards']}  "
        f"Chief: {report['totals']['chief_called']}  Routing: {report['totals']['routing_attempts']}",
        "",
    ]
    for source, row in report["sources"].items():
        lines.append(
            f"[{source}] ingest={row['ingested']} cards={row['cards']} "
            f"chief={row['chief_called']} low_conf={row['low_confidence']}"
        )
        if row["drops"]:
            lines.append(f"  drops: {json.dumps(row['drops'], ensure_ascii=False, sort_keys=True)}")
        if row["routing_skips"]:
            lines.append(f"  routing_skips: {json.dumps(row['routing_skips'], ensure_ascii=False, sort_keys=True)}")
        if row["headline_vs_final"]:
            lines.append(f"  headline_vs_final: {json.dumps(row['headline_vs_final'], ensure_ascii=False, sort_keys=True)}")
        if row["phase_prior_vs_headline"]:
            lines.append(
                f"  prior_vs_headline: {json.dumps(row['phase_prior_vs_headline'], ensure_ascii=False, sort_keys=True)}"
            )
        if row["verdicts"]:
            lines.append(f"  verdicts: {json.dumps(row['verdicts'], ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args()
    report = summarize()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))


if __name__ == "__main__":
    main()
