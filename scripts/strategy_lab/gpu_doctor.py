# -*- coding: utf-8 -*-
"""GPU runtime doctor: report whether the sweep GPU backend is usable.

Honest and read-only. Prints capability detection (cupy/torch/numba), the
detected device, the reason if unavailable, the safe fallback, and how each
requested backend (cpu/gpu/auto) would resolve right now. It never enables live
trading, touches secrets, or pretends CPU is GPU.

    python -m scripts.strategy_lab.gpu_doctor
    python -m scripts.strategy_lab.gpu_doctor --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.gpu_runtime import (  # noqa: E402
    GPU_SUPPORTED_FAMILIES,
    doctor,
    resolve_backend,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="Report GPU backend capability for the sweep worker.")
    ap.add_argument("--json", action="store_true", help="Emit a JSON report")
    args = ap.parse_args()

    cap = doctor()
    resolutions = {b: resolve_backend(b).to_dict() for b in ("cpu", "gpu", "auto")}

    if args.json:
        print(json.dumps({
            "capability": cap,
            "resolutions": resolutions,
            "gpu_supported_families": list(GPU_SUPPORTED_FAMILIES),
        }, ensure_ascii=False, indent=2))
        return

    print("=" * 60)
    print("  Strategy Lab GPU Doctor")
    print("=" * 60)
    print(f"  gpu_available : {cap['gpu_available']}")
    print(f"  backend_name  : {cap['backend_name']}")
    print(f"  device_name   : {cap['device_name'] or '-'}")
    print(f"  nvidia_smi    : {cap['nvidia_smi_present']} ({cap['detail'].get('nvidia_smi_name') or 'n/a'})")
    print(f"  safe_fallback : {cap['safe_fallback']}")
    if not cap["gpu_available"]:
        print(f"  reason        : {cap['reason_if_unavailable']}")
    print()
    print("  Requested backend -> effective:")
    for b, r in resolutions.items():
        line = f"    {b:5} -> {r['effective_backend']}"
        if r["error"]:
            line += f"  ERROR: {r['error']}"
        elif r["fallback_reason"]:
            line += f"  ({r['fallback_reason']})"
        print(line)
    print()
    print(f"  GPU-accelerated families: {', '.join(GPU_SUPPORTED_FAMILIES)}")
    print("  (other families run on the CPU scalar path; trade simulation is CPU-only)")
    if not cap["gpu_available"]:
        print()
        print("  Recovery: install a GPU compute backend, e.g.")
        print("    pip install cupy-cuda12x      # NVIDIA CUDA 12.x")
        print("  then re-run this doctor. No live trading, no secrets involved.")
    print("=" * 60)


if __name__ == "__main__":
    main()
