"""Build the private lineage graph viewer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.graph_viewer import write_lineage_graph_viewer  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--max-links", type=int, default=500)
    parser.add_argument("--allow-public-output", action="store_true")
    args = parser.parse_args()

    result = write_lineage_graph_viewer(
        args.private_root,
        max_links=args.max_links,
        allow_public_output=args.allow_public_output,
    )
    print(
        "lineage graph viewer written="
        f"{result['viewer_label']} nodes={result['nodes']} edges={result['edges']} links={result['links']}"
    )
    print(result["viewer_file"])


if __name__ == "__main__":
    main()
