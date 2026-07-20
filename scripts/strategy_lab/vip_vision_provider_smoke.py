"""Private smoke check for the VIP screenshot vision provider.

Default mode is status-only. A real provider call happens only with --apply and
an explicit --image path. Raw model text is written only under the private
Strategy Lab root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402

if __name__ == "__main__":
    load_runtime_dotenv(ROOT)

from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402
from src.utils.llm_formatter import generate_premium_analysis, premium_vision_status  # noqa: E402


def _summary_path(private_root: Path) -> Path:
    return private_root / "reports" / "provider_bench" / "vip_vision_provider_smoke.json"


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    private_root = resolve_private_root(args.private_root)
    status = premium_vision_status()
    result_text = ""
    error = ""
    called_provider = False
    if args.apply:
        if not args.image:
            error = "image_required_for_apply"
        else:
            image_path = Path(args.image)
            if not image_path.exists():
                error = "image_not_found"
            elif not bool(status.get("configured")):
                error = "provider_not_configured"
            else:
                try:
                    result_text = await generate_premium_analysis(args.category, image_path.read_bytes()) or ""
                    called_provider = True
                except Exception as exc:  # noqa: BLE001 - provider smoke must not crash control flow
                    error = str(exc).strip() or type(exc).__name__
    summary = {
        "schema": "VipVisionProviderSmoke.v1",
        "configured": bool(status.get("configured")),
        "provider": status.get("active_provider") or status.get("provider"),
        "model_label": status.get("model_label"),
        "provider_scope": status.get("provider_scope"),
        "called_provider": called_provider,
        "has_result": bool(result_text),
        "result_chars": len(result_text),
        "error": error,
        "private_path_label": "strategy-lab/reports/provider_bench/vip_vision_provider_smoke.json",
        "paper_only": True,
        "execution_allowed": False,
    }
    out = {
        **summary,
        "provider_status": status,
        "raw_result": result_text,
    }
    path = _summary_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--image", default="")
    parser.add_argument("--category", default="crypto")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = asyncio.run(run_smoke(args))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "vip_vision_provider_smoke: "
            f"configured={summary['configured']} provider={summary['provider']} "
            f"called={summary['called_provider']} has_result={summary['has_result']} "
            f"error={summary['error'] or '-'} path={summary['private_path_label']}"
        )


if __name__ == "__main__":
    main()
