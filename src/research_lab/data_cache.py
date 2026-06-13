# -*- coding: utf-8 -*-
"""Cache/inventory check for prepared 1m data — read-only, no download.

Given DataRequirements, locate existing local 1m files (canonical OHLCV JSON) and
resolve each requirement's status: present / missing / too_short / malformed.
Coverage is measured by counting the deduped 1m bars that actually fall inside the
requested window, so a file that exists but does not cover the window is `missing`,
and a partial file is `too_short`. Nothing is fetched here.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.research_lab.data_inventory import inspect_file
from src.research_lab.data_requirements import (
    MALFORMED,
    MISSING,
    OUTSIDE_POLICY,
    PRESENT,
    TOO_SHORT,
    DataRequirement,
)
from src.research_lab.experiment import load_candles

DEFAULT_MIN_COVERAGE_RATIO = 0.9


@dataclass(frozen=True)
class CacheCheck:
    requirement: DataRequirement
    file_label: str
    bars_in_window: int
    covered: bool

    def to_dict(self) -> dict[str, Any]:
        d = self.requirement.to_dict()
        d.update(file_label=self.file_label, bars_in_window=self.bars_in_window, covered=self.covered)
        return d


def _files_for_symbol(data_dir: Path, symbol: str) -> list[str]:
    norm = symbol.replace("-", "_").replace("/", "_")
    return sorted(glob.glob(str(data_dir / f"{norm}_*_1m*.json")))


def check_requirement(
    req: DataRequirement,
    *,
    data_dir: Path,
    min_coverage_ratio: float = DEFAULT_MIN_COVERAGE_RATIO,
) -> CacheCheck:
    """Resolve one requirement's cache status without downloading anything."""
    if req.status == OUTSIDE_POLICY:
        return CacheCheck(req, "", 0, False)

    files = _files_for_symbol(data_dir, req.symbol)
    if not files:
        return CacheCheck(replace(req, status=MISSING), "", 0, False)

    seen_ts: set[int] = set()
    label = ""
    malformed_seen = False
    any_1m = False
    for f in files:
        path = Path(f)
        info = inspect_file(path)
        if info.get("quality_status") == "malformed" or not info.get("has_ohlcv"):
            malformed_seen = True
            continue
        if info.get("timeframe") != "1m":
            continue
        any_1m = True
        try:
            candles = load_candles(path)
        except Exception:
            malformed_seen = True
            continue
        for candle in candles:
            ts = int(candle["ts"])
            if req.start_ts <= ts <= req.end_ts and ts not in seen_ts:
                seen_ts.add(ts)
                if not label:
                    label = str(info.get("source_file_label") or path.name)

    bars = len(seen_ts)
    if bars == 0:
        status = MALFORMED if (malformed_seen and not any_1m) else MISSING
        return CacheCheck(replace(req, status=status), label, 0, False)

    needed = max(1, int(min_coverage_ratio * req.bar_count()))
    covered = bars >= needed
    return CacheCheck(replace(req, status=PRESENT if covered else TOO_SHORT), label, bars, covered)


def resolve_requirements(
    reqs: list[DataRequirement],
    *,
    data_dir: Path,
    min_coverage_ratio: float = DEFAULT_MIN_COVERAGE_RATIO,
) -> list[CacheCheck]:
    return [check_requirement(r, data_dir=data_dir, min_coverage_ratio=min_coverage_ratio) for r in reqs]
