"""Public-safe inventory of numeric GPU coverage; no runtime or network calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.research_lab.gpu_runtime import (  # noqa: E402
    AUTO_GPU_MIN_WORK_ITEMS,
    GPU_SUPPORTED_FAMILIES,
)
from src.research_lab.strategy_registry import list_strategies  # noqa: E402


def build_inventory() -> dict:
    implemented = set(GPU_SUPPORTED_FAMILIES)
    rows = []
    for item in list_strategies():
        rows.append({
            "strategy_id": item.strategy_id,
            "group": item.family,
            "signal_backend": "gpu_or_cpu_auto" if item.strategy_id in implemented else "cpu",
            "gpu_signal_kernel": item.strategy_id in implemented,
            "gpu_fixed_exit_simulation": True,
            "required_data": list(item.required_data),
        })
    return {
        "schema": "GpuStrategyInventory.v1",
        "strategies": len(rows),
        "gpu_signal_kernels": len(implemented),
        "cpu_signal_only": len(rows) - len(implemented),
        "auto_gpu_min_work_items": AUTO_GPU_MIN_WORK_ITEMS,
        "rows": rows,
        "paper_only": True,
        "execution_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_inventory()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"GPU signal kernels: {report['gpu_signal_kernels']}/{report['strategies']} | "
            f"CPU-only signals: {report['cpu_signal_only']} | "
            f"auto threshold: {report['auto_gpu_min_work_items']} bar-variants"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
