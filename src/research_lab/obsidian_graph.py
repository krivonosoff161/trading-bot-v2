# -*- coding: utf-8 -*-
"""Private Obsidian graph notes for promoted (non-REJECT) candidates.

Writes one markdown note per non-REJECT candidate under the PRIVATE research root
(`strategy-lab/obsidian/`), with wikilinks to symbol, family, regime, verdict and
related symbols. Notes hold research labels and next-test suggestions only — never
a profitability claim — and never leave the private root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.research_lab.paths import resolve_private_root

REGISTRY_STATUSES = {"FORWARD_PAPER", "REGIME_SPECIFIC", "OBSERVE"}
_STATUS_LABEL = {
    "FORWARD_PAPER": "forward-paper",
    "REGIME_SPECIFIC": "observe (regime-specific)",
    "OBSERVE": "observe",
}


def obsidian_dir(private_root: Path) -> Path:
    return private_root / "obsidian"


def _safe(value: str) -> str:
    return "".join(c if (c.isalnum() or c in {"_", "-", "."}) else "_" for c in str(value)).strip("_") or "unknown"


def build_candidate_note(entry: dict[str, Any], *, related: tuple[str, ...] = ()) -> tuple[str, str]:
    """Return (filename, markdown) for one candidate registry entry."""
    cid = str(entry.get("candidate_id") or "unknown")
    symbol = str(entry.get("symbol") or "unknown")
    family = str(entry.get("strategy_id") or "unknown")
    status = str(entry.get("validation_status") or "OBSERVE")
    regime = ""
    summary = entry.get("regime_summary")
    if isinstance(summary, dict):
        regime = str(summary.get("dominant_bucket") or "")
    reasons = entry.get("validation_reasons") or []
    related_links = ", ".join(f"[[Symbols/{_safe(s)}]]" for s in related) or "-"
    lines = [
        f"# Candidate {cid}",
        "",
        "Private research note. Research label only, not a profitability claim.",
        "",
        f"- strategy: [[Families/{_safe(family)}]]",
        f"- symbol: [[Symbols/{_safe(symbol)}]]",
        f"- regime: {f'[[Regime/{_safe(regime)}]]' if regime else '-'}",
        f"- verdict: [[Verdict/{_safe(status)}]]",
        f"- status: {_STATUS_LABEL.get(status, 'observe')}",
        f"- reason codes: {', '.join(f'[[Reasons/{_safe(r)}]]' for r in reasons) or '-'}",
        f"- related symbols: {related_links}",
        f"- linked run: {entry.get('artifact_label') or '-'}",
        f"- next test: {entry.get('next_action') or '-'}",
        f"- review due: {entry.get('next_review') or '-'}",
        "",
    ]
    return f"{_safe(cid)}_{_safe(symbol)}_{_safe(family)}.md", "\n".join(lines)


def write_candidate_notes(
    private_root: Path,
    entries: list[dict[str, Any]],
    *,
    related_for: Any = None,
    allow_public_output: bool = False,
) -> dict[str, Any]:
    """Write one note per non-REJECT candidate. `related_for(symbol)->tuple`.

    The registry keys rows by (experiment_id, candidate_id), so the same candidate
    can be graded by more than one experiment. Notes are keyed by candidate
    identity (filename), so collapse to one note per file (last grading wins) and
    report the true count of files written, not the number of rows seen.
    """
    private_root = resolve_private_root(private_root, allow_public_output=allow_public_output)
    out_dir = obsidian_dir(private_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    notes: dict[str, str] = {}
    for entry in entries:
        if str(entry.get("validation_status")) not in REGISTRY_STATUSES:
            continue
        related = tuple(related_for(str(entry.get("symbol") or ""))) if related_for else ()
        filename, markdown = build_candidate_note(entry, related=related)
        notes[filename] = markdown
    for filename, markdown in notes.items():
        (out_dir / filename).write_text(markdown, encoding="utf-8")
    return {"notes_written": len(notes), "notes_label": "strategy-lab/obsidian"}


def count_notes(private_root: Path) -> int:
    out_dir = obsidian_dir(private_root)
    if not out_dir.exists():
        return 0
    return sum(1 for _ in out_dir.glob("*.md"))
