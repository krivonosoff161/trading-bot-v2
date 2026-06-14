# -*- coding: utf-8 -*-
"""Build a private standalone HTML graph viewer for Strategy Lab candidates."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.candidate_registry import load_entries, registry_path  # noqa: E402
from src.research_lab.graph_viewer import write_graph_viewer  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--max-candidates", type=int, default=350)
    ap.add_argument("--allow-public-output", action="store_true")
    args = ap.parse_args()

    private_root = resolve_private_root(Path(args.private_root), allow_public_output=args.allow_public_output)
    entries = load_entries(registry_path(private_root))
    result = write_graph_viewer(
        private_root,
        entries,
        max_candidates=max(1, args.max_candidates),
        allow_public_output=args.allow_public_output,
    )
    print(
        "graph viewer written="
        f"{result['viewer_label']} nodes={result['nodes']} edges={result['edges']} candidates={result['candidates']}"
    )
    print(result["viewer_file"])


if __name__ == "__main__":
    main()
