# -*- coding: utf-8 -*-
"""Obsidian farm-memory notes — the farm's research memory, not just a candidate dump.

Writes deterministic markdown notes under the PRIVATE root
(`obsidian-vault/Farm/{Runs,Symbols,Families,Candidates}`) from farm_results + the
candidate registry: a daily run note, a per-symbol note, a per-family note (hypothesis
/ required data / GPU support / failure modes / aggregate stats), and per-candidate
notes (exact params from the registry). Graph wikilinks connect symbol -> family ->
candidate, with [[funding]] / [[OI]] / [[FVG]] / [[VWAP]] data tags. Research labels
only, never a profitability claim, never absolute paths, never leaves the private root.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.research_lab.candidate_registry import registry_path
from src.research_lab.gpu_runtime import GPU_SUPPORTED_FAMILIES
from src.research_lab.paths import resolve_private_root
from src.research_lab.state_db import default_db_path

_REGISTRY_STATUSES = {"FORWARD_PAPER", "REGIME_SPECIFIC", "OBSERVE", "PROMOTE_FOR_PRESSURE_TEST"}


def _safe(value: str) -> str:
    return "".join(c if (c.isalnum() or c in {"_", "-", "."}) else "_" for c in str(value)).strip("_") or "unknown"


def _data_tags(family: str, required: tuple[str, ...]) -> list[str]:
    tags = []
    token_tag = {"funding": "funding", "oi": "OI", "microstructure": "microstructure"}
    for token in required:
        if token in token_tag:
            tags.append(f"[[{token_tag[token]}]]")
    for needle, tag in (("fvg", "FVG"), ("vwap", "VWAP"), ("bb", "BollingerBands"), ("fractal", "Fractals")):
        if needle in family and f"[[{tag}]]" not in tags:
            tags.append(f"[[{tag}]]")
    return tags


def _read_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, family, timeframe, decision, validation_status, asset_group, backend, "
            "hard_status, n_trades, avg_net_pct, profit_factor, max_drawdown_pct, data_file, candidate_id "
            "FROM farm_results")]
        conn.close()
        return rows
    except sqlite3.Error:
        return []


def _registry_params(private_root: Path) -> dict[str, dict[str, Any]]:
    path = registry_path(private_root)
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[str(entry.get("candidate_id") or "")] = entry
    return out


def _daily_note(day: str, rows: list[dict[str, Any]]) -> str:
    decisions: dict[str, int] = {}
    groups: dict[str, int] = {}
    backends: dict[str, int] = {}
    for r in rows:
        decisions[r["decision"]] = decisions.get(r["decision"], 0) + 1
        groups[r["asset_group"] or "-"] = groups.get(r["asset_group"] or "-", 0) + 1
        backends[r["backend"] or "-"] = backends.get(r["backend"] or "-", 0) + 1
    top = sorted((r for r in rows if r["validation_status"] in _REGISTRY_STATUSES),
                 key=lambda r: float(r.get("avg_net_pct") or 0.0), reverse=True)[:10]
    needs = {d: n for d, n in decisions.items() if d.startswith("NEEDS_")}
    lines = [f"# Farm Run {day}", "", "Daily research memory. Labels only, not a profitability claim.", "",
             f"- candidates: {len(rows)}  symbols: {len({r['symbol'] for r in rows})}  "
             f"families: {len({r['family'] for r in rows})}",
             f"- decisions: {decisions}", f"- by group: {groups}", f"- backends: {backends}",
             f"- needs-data: {needs or '-'}", "", "## Top candidates", ""]
    for r in top:
        lines.append(f"- [[Symbols/{_safe(r['symbol'])}]] -> [[Families/{_safe(r['family'])}]] "
                     f"({r['validation_status']}, avg_net={r['avg_net_pct']}, pf={r['profit_factor']})")
    lines += ["", "## Next", "", "- review FORWARD_PAPER/REGIME_SPECIFIC via export_hard_validation_requests",
              "- provide OI/funding data for NEEDS_*_DATA families", ""]
    return "\n".join(lines)


def _symbol_note(symbol: str, rows: list[dict[str, Any]]) -> str:
    mine = [r for r in rows if r["symbol"] == symbol]
    group = next((r["asset_group"] for r in mine if r["asset_group"]), "-")
    tfs = sorted({r["timeframe"] for r in mine if r["timeframe"]})
    fams = sorted({r["family"] for r in mine})
    best = max(mine, key=lambda r: float(r.get("avg_net_pct") or 0.0), default=None)
    val = sorted({r["validation_status"] for r in mine if r["validation_status"]})
    lines = [f"# {symbol}", "", f"- group: {group}", f"- timeframes: {', '.join(tfs) or '-'}",
             f"- families tested: {', '.join(f'[[Families/{_safe(f)}]]' for f in fams)}",
             f"- validation states: {', '.join(val) or '-'}",
             f"- best avg_net: {best['avg_net_pct'] if best else '-'} ({best['family'] if best else '-'})", ""]
    return "\n".join(lines)


def _family_note(family: str, rows: list[dict[str, Any]]) -> str:
    mine = [r for r in rows if r["family"] == family]
    decisions: dict[str, int] = {}
    for r in mine:
        decisions[r["decision"]] = decisions.get(r["decision"], 0) + 1
    hypothesis, risk, required = "-", "-", ()
    try:
        from src.research_lab.strategy_registry import get_strategy
        sdef = get_strategy(family)
        hypothesis, risk, required = sdef.description, sdef.risk_notes, sdef.required_data
    except ValueError:
        pass
    gpu = "yes" if family in GPU_SUPPORTED_FAMILIES else "no (CPU signal path)"
    tags = _data_tags(family, required)
    lines = [f"# {family}", "", f"- hypothesis: {hypothesis}", f"- required data: {', '.join(required) or 'OHLCV only'}",
             f"- GPU signal kernel: {gpu}", f"- known failure modes: {risk}",
             f"- data tags: {', '.join(tags) or '-'}", f"- aggregate decisions: {decisions}",
             f"- candidates: {len(mine)}", ""]
    return "\n".join(lines)


def _candidate_note(row: dict[str, Any], entry: dict[str, Any]) -> str:
    params = entry.get("params") if entry else {}
    lines = [f"# Candidate {row['candidate_id']}", "",
             "Research label only, not a profitability claim.", "",
             f"- symbol: [[Symbols/{_safe(row['symbol'])}]]",
             f"- family: [[Families/{_safe(row['family'])}]]",
             f"- timeframe: {row['timeframe'] or '-'}",
             f"- params: `{json.dumps(params, sort_keys=True)}`" if params else "- params: (in run artifact)",
             f"- decision: {row['decision']}  validation: {row['validation_status']}  hard: {row['hard_status'] or '-'}",
             f"- metrics: n_trades={row['n_trades']} avg_net={row['avg_net_pct']} pf={row['profit_factor']} "
             f"maxDD={row['max_drawdown_pct']}",
             f"- data file: {row['data_file'] or '-'}", ""]
    return "\n".join(lines)


def write_farm_notes(private_root: Path, *, day: str, allow_public_output: bool = False) -> dict[str, Any]:
    """Write daily/symbol/family/candidate farm-memory notes. Deterministic per id."""
    rows = _read_rows(default_db_path(Path(private_root)))
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    base = private_root / "obsidian-vault" / "Farm"
    for sub in ("Runs", "Symbols", "Families", "Candidates"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    counts = {"runs": 0, "symbols": 0, "families": 0, "candidates": 0}
    (base / "Runs" / f"farm_{_safe(day)}.md").write_text(_daily_note(day, rows), encoding="utf-8")
    counts["runs"] = 1
    for symbol in sorted({r["symbol"] for r in rows}):
        (base / "Symbols" / f"{_safe(symbol)}.md").write_text(_symbol_note(symbol, rows), encoding="utf-8")
        counts["symbols"] += 1
    for family in sorted({r["family"] for r in rows}):
        (base / "Families" / f"{_safe(family)}.md").write_text(_family_note(family, rows), encoding="utf-8")
        counts["families"] += 1
    registry = _registry_params(private_root)
    for row in rows:
        if row["validation_status"] not in _REGISTRY_STATUSES:
            continue
        cid = str(row["candidate_id"])
        fname = f"{_safe(cid)}_{_safe(row['symbol'])}_{_safe(row['family'])}.md"
        (base / "Candidates" / fname).write_text(_candidate_note(row, registry.get(cid, {})), encoding="utf-8")
        counts["candidates"] += 1
    return {"counts": counts, "label": "strategy-lab/obsidian-vault/Farm"}
