# -*- coding: utf-8 -*-
"""Build private Obsidian graph notes for promoted (non-REJECT) candidates.

Reads the private candidate registry, enriches each candidate with universe
relation hints, and writes one markdown note per non-REJECT candidate under the
private research root. No public output, no profitability claims, no API calls.

    python -m scripts.strategy_lab.build_obsidian_graph
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.candidate_registry import load_entries, registry_path  # noqa: E402
from src.research_lab.obsidian_graph import write_candidate_notes  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.research_lab.universe import load_universe  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    entries = load_entries(registry_path(private_root))
    try:
        universe = load_universe()
        related_for = lambda symbol: universe.related(symbol)  # noqa: E731
    except Exception:
        related_for = None
    result = write_candidate_notes(
        private_root, entries, related_for=related_for, allow_public_output=args.allow_public_output
    )
    print(f"obsidian notes written={result['notes_written']} dir={result['notes_label']}")


if __name__ == "__main__":
    main()
