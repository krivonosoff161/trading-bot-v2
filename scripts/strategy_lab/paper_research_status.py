"""Print sanitized status for the paper/research backbone."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.paper_research_status import build_status  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status = build_status(args.private_root)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
        return
    lineage = status["lineage"]
    print("paper_research_status:")
    print(f"  scanner_events={lineage['scanner_events']['rows']}")
    print(f"  data_packets={lineage['data_packets']['rows']}")
    print(f"  feature_packets={lineage['feature_packets']['rows']}")
    print(f"  cycle_links={lineage['cycle_links']['rows']}")
    print(f"  backfill_rows={status['backfill']['rows']}")
    print(f"  feedback_rows={status['feedback']['rows']}")
    print(f"  agent_roles={status['agent_roles']['roles']}")
    print(f"  provider_bench_rows={status['provider_bench'].get('rows', 0)}")
    review_cycle = status["agent_role_review_cycle"]
    print(
        "  agent_role_review_cycle="
        f"reviews={review_cycle.get('reviews', 0)} "
        f"accepted={review_cycle.get('accepted', 0)} "
        f"configured={review_cycle.get('configured', False)}"
    )
    vision = status["vip_vision_smoke"]
    print(
        "  vip_vision_smoke="
        f"configured={vision.get('configured', False)} "
        f"called={vision.get('called_provider', False)} "
        f"has_result={vision.get('has_result', False)}"
    )
    reviews = status["llm_role_reviews"]["roles"]
    print(
        "  llm_reviews="
        + ",".join(f"{name}:{row['accepted']}/{row['rows']}" for name, row in sorted(reviews.items()))
    )
    print(f"  execution_allowed={status['execution_allowed']}")


if __name__ == "__main__":
    main()
