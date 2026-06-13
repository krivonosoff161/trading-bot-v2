# -*- coding: utf-8 -*-
"""Prepare-on-demand orchestration + canonical 1m writer.

Ties requirements -> cache check -> provider -> writer. Dry-run writes nothing and
only reports what WOULD be downloaded. Apply downloads (via the chosen provider)
and writes only the missing/partial windows, capped by policy. The writer produces
the canonical OHLCV JSON (ts/date/open/high/low/close/vol), deduped and sorted,
atomically, under the private data root only — never the public repo, never an
order or account endpoint, never a paid LLM.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.research_lab.data_cache import CacheCheck
from src.research_lab.data_requirements import (
    MALFORMED,
    MISSING,
    OUTSIDE_POLICY,
    PRESENT,
    TOO_SHORT,
)
from src.research_lab.market_data_provider import MarketDataProvider, ProviderNotConfigured
from src.research_lab.paths import one_minute_data_dir, resolve_private_root

_REQUIRED_KEYS = ("ts", "open", "high", "low", "close", "vol")
_NEEDS_DATA = {MISSING, TOO_SHORT, MALFORMED}


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep valid candle rows, dedup by ts, sort ascending. Preserves extra fields."""
    by_ts: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            ts = int(row["ts"])
            for key in _REQUIRED_KEYS[1:]:
                float(row[key])
        except (KeyError, TypeError, ValueError):
            continue
        clean = dict(row)
        clean["ts"] = ts
        if "date" not in clean:
            clean["date"] = dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc).isoformat()
        by_ts[ts] = clean
    return [by_ts[t] for t in sorted(by_ts)]


def slice_filename(symbol: str, start_ts: int, end_ts: int) -> str:
    norm = str(symbol).replace("-", "_").replace("/", "_")
    return f"{norm}_{int(start_ts)}_{int(end_ts)}_1m.json"


def write_1m_candles(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    start_ts: int,
    end_ts: int,
    data_dir: Path,
) -> tuple[Path, int]:
    """Write/merge canonical 1m candles atomically. Returns (path, bars_written)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = data_dir / slice_filename(symbol, start_ts, end_ts)

    merged = list(rows)
    if out_path.exists():  # preserve + dedup with any existing slice
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                merged = existing + merged
        except (OSError, json.JSONDecodeError):
            pass
    candles = _normalize_rows(merged)

    tmp = out_path.with_name(f".{out_path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(candles, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out_path)
    return out_path, len(candles)


@dataclass(frozen=True)
class PrepareReport:
    provider: str
    provider_configured: bool
    apply: bool
    counts: dict[str, int] = field(default_factory=dict)
    would_download: int = 0
    downloaded: int = 0
    files_written: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "strategy_lab_prepare_1m.v1",
            "provider": self.provider,
            "provider_configured": self.provider_configured,
            "mode": "apply" if self.apply else "dry_run",
            "counts": self.counts,
            "would_download": self.would_download,
            "downloaded": self.downloaded,
            "files_written": self.files_written,
            "missing": int(self.counts.get(MISSING, 0) + self.counts.get(TOO_SHORT, 0) + self.counts.get(MALFORMED, 0)),
            "items": self.items,
            "skipped": self.skipped,
            "note": "research data only; no live trading, no order endpoints, no paid LLM",
        }


def prepare(
    checks: list[CacheCheck],
    *,
    provider: MarketDataProvider,
    private_root: Path,
    apply: bool,
    allow_public_output: bool = False,
) -> PrepareReport:
    """Resolve each requirement into an action; only apply+configured writes data."""
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    data_dir = one_minute_data_dir(private_root)
    configured = bool(getattr(provider, "configured", False))

    counts: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    files_written: list[str] = []
    would_download = 0
    downloaded = 0

    for check in checks:
        req = check.requirement
        counts[req.status] = counts.get(req.status, 0) + 1
        item = check.to_dict()

        if req.status == PRESENT:
            item["action"] = "present"
        elif req.status == OUTSIDE_POLICY:
            item["action"] = "skipped_outside_policy"
            skipped.append({"symbol": req.symbol, "reason": OUTSIDE_POLICY})
        elif req.status in _NEEDS_DATA:
            if not apply:
                item["action"] = "would_download"
                would_download += 1
            elif not configured:
                item["action"] = "provider_not_configured"
                skipped.append({"symbol": req.symbol, "reason": "provider_not_configured"})
            else:
                try:
                    rows = provider.fetch_ohlcv(req.symbol, "1m", req.start_ts, req.end_ts)
                except ProviderNotConfigured:
                    item["action"] = "provider_not_configured"
                    skipped.append({"symbol": req.symbol, "reason": "provider_not_configured"})
                    rows = None
                except Exception as exc:  # provider/runtime error: never crash the run
                    item["action"] = "provider_error"
                    skipped.append({"symbol": req.symbol, "reason": f"provider_error:{type(exc).__name__}"})
                    rows = None
                if rows is not None:
                    if not rows:
                        item["action"] = "no_data_returned"
                        skipped.append({"symbol": req.symbol, "reason": "no_data_returned"})
                    else:
                        path, bars = write_1m_candles(
                            rows, symbol=req.symbol, start_ts=req.start_ts, end_ts=req.end_ts, data_dir=data_dir,
                        )
                        label = f"market_data/1m/{path.name}"
                        item["action"] = "downloaded"
                        item["written_label"] = label
                        item["bars_written"] = bars
                        files_written.append(label)
                        downloaded += 1
        else:
            item["action"] = "unknown"
        items.append(item)

    return PrepareReport(
        provider=getattr(provider, "name", "null"),
        provider_configured=configured,
        apply=apply,
        counts=counts,
        would_download=would_download,
        downloaded=downloaded,
        files_written=files_written,
        items=items,
        skipped=skipped,
    )


def prepare_report_path(private_root: Path) -> Path:
    return private_root / "reports" / "data_prep" / "last_prepare_1m.json"


def write_prepare_report(private_root: Path, report: PrepareReport, *, allow_public_output: bool = False) -> Path:
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    path = prepare_report_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report.to_dict())
    payload["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_prepare_report(private_root: Path) -> dict[str, Any]:
    path = prepare_report_path(private_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
