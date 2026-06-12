# -*- coding: utf-8 -*-
"""Build a data inventory for a strategy-lab experiment spec."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.data_inventory import build_inventory, write_inventory  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="Experiment spec JSON with data_glob/symbols")
    ap.add_argument(
        "--out-root",
        default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)),
        help="Private strategy-lab root (inventory goes to <root>/inventory)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print summary only, write nothing")
    ap.add_argument("--allow-public-output", action="store_true", help="Allow writing under this public repo")
    args = ap.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    inventory = build_inventory(
        str(spec["data_glob"]),
        [str(s) for s in spec.get("symbols", [])],
    )
    counts = inventory["quality_counts"]
    print(
        f"inventory files={inventory['file_count']} "
        + " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )
    if args.dry_run:
        return
    out_root = resolve_private_root(args.out_root, allow_public_output=args.allow_public_output)
    out_path = write_inventory(inventory, out_root / "inventory")
    print(f"wrote {out_path.parent.name}/{out_path.name} (private root)")


if __name__ == "__main__":
    main()
