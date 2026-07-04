"""Observe the paper-only main runtime queue on public candles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.main_paper_runtime import observe_main_paper_runtime  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402
from src.research_lab.providers.okx_public import OkxPublicMarketDataProvider  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper-only observer for main paper runtime queue.")
    ap.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--apply", action="store_true", help="persist observation snapshot/jsonl")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--public-fetch-timeout", type=float, default=10.0)
    args = ap.parse_args()

    provider = OkxPublicMarketDataProvider(timeout=args.public_fetch_timeout)
    summary = observe_main_paper_runtime(
        args.private_root,
        limit=args.limit,
        apply=args.apply,
        provider=provider,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(
        "main_paper_runtime "
        f"read={summary['rows_read']} observed={summary['observed']} "
        f"reviewed={summary['reviewed']} pending={summary['pending']} "
        f"invalid={summary['invalid']} provider_error={summary['provider_error']} "
        f"execution_allowed={summary['execution_allowed']} paper_only={summary['paper_only']}"
    )
    if args.apply:
        print(summary["snapshot_path"])


if __name__ == "__main__":
    main()
