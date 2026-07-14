# -*- coding: utf-8 -*-
"""GPU runtime capability detection and a honest backend contract.

The strategy-lab sweep numeric core can run on a GPU array backend (cupy / torch /
numba.cuda) when one is really available. This module does the detection, never
pretends CPU is GPU, and gives a deterministic CPU fallback with an explicit
reason when no GPU backend is usable.

Detection is lazy and defensive: a missing optional library is a clean
"unavailable", never an import error. No heavy dependency is required to import
this module (only the stdlib + numpy, which is already a project dependency).

Nothing here touches live trading, the order engine, secrets, or the network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# Backend tokens shared with sweep_spec.BACKENDS.
CPU = "cpu"
GPU = "gpu"
AUTO = "auto"

# Strategy families whose numeric signal core has a GPU-accelerated kernel.
# Everything else runs on the CPU scalar path (documented support matrix).
# Each entry is parity-tested (scalar == vectorized) before being listed here.
# Deliberately NOT listed: volatility_squeeze_breakout_v2 — its ATR-percentile gate is
# a sequential Wilder recurrence (not data-parallel), so a kernel would add risk with
# no real speedup; it stays on the CPU signal path with an honest cpu_fallback record.
GPU_SUPPORTED_FAMILIES = (
    "momentum_breakout",
    "donchian_breakout",
    "range_breakout",
    "volatility_squeeze_breakout",
    "mean_reversion_fade",
    "volume_shock_continuation",
    "impulse_continuation",
    "range_volume_breakout",
    "pump_dump_scalp",
)
AUTO_GPU_MIN_WORK_ITEMS = 8_000


def auto_gpu_worthwhile(n_bars: int, n_variants: int, *, threshold: int | None = None) -> bool:
    """Use GPU automatically only for a sufficiently large reusable batch."""
    limit = AUTO_GPU_MIN_WORK_ITEMS if threshold is None else max(1, int(threshold))
    return max(0, int(n_bars)) * max(0, int(n_variants)) >= limit


@dataclass(frozen=True)
class GpuCapability:
    """Result of probing the machine for a usable GPU compute backend."""

    gpu_available: bool
    backend_name: str  # "cupy" | "torch" | "numba" | "none"
    device_name: str
    reason_if_unavailable: str
    safe_fallback: str = CPU
    nvidia_smi_present: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu_available": self.gpu_available,
            "backend_name": self.backend_name,
            "device_name": self.device_name,
            "reason_if_unavailable": self.reason_if_unavailable,
            "safe_fallback": self.safe_fallback,
            "nvidia_smi_present": self.nvidia_smi_present,
            "detail": dict(self.detail),
        }


def _nvidia_smi_name() -> str | None:
    """First GPU name from nvidia-smi, or None when the tool/GPU is absent."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    first = (out.stdout or "").strip().splitlines()
    return first[0].strip() if first else ""


def _register_nvidia_dll_dirs() -> None:
    """Make pip-installed CUDA libs (nvidia-*-cu12 wheels) discoverable on Windows.

    cupy loads CUDA DLLs (e.g. nvrtc64_120_0.dll) by name; the pip CUDA wheels put
    them under site-packages/nvidia/*/bin. Registering those dirs via
    os.add_dll_directory lets cupy find them without a system CUDA install. No-op
    off Windows or when the wheels are absent.
    """
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    try:
        import site
        bases = list(site.getsitepackages())
        user = site.getusersitepackages()
        if user:
            bases.append(user)
    except Exception:  # noqa: BLE001
        return
    seen: set[str] = set()
    for base in bases:
        nvidia = os.path.join(base, "nvidia")
        if not os.path.isdir(nvidia):
            continue
        for entry in os.listdir(nvidia):
            bin_dir = os.path.join(nvidia, entry, "bin")
            if bin_dir not in seen and os.path.isdir(bin_dir):
                seen.add(bin_dir)
                try:
                    os.add_dll_directory(bin_dir)
                except OSError:
                    pass
                # nvrtc loads its sibling builtins DLL via the process PATH, which
                # add_dll_directory does not cover, so prepend PATH as well.
                if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
                    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def _probe_cupy() -> tuple[bool, str, dict[str, Any]]:
    _register_nvidia_dll_dirs()
    try:
        import cupy  # type: ignore
    except Exception as exc:  # noqa: BLE001 - missing/broken lib is a clean miss
        return False, "", {"cupy_import_error": str(exc)[:200]}
    try:
        count = int(cupy.cuda.runtime.getDeviceCount())
        if count <= 0:
            return False, "", {"cupy_device_count": 0}
        props = cupy.cuda.runtime.getDeviceProperties(0)
        name = props.get("name", b"")
        device = name.decode() if isinstance(name, (bytes, bytearray)) else str(name)
    except Exception as exc:  # noqa: BLE001 - cupy present but no usable CUDA runtime
        return False, "", {"cupy_runtime_error": str(exc)[:200]}
    # A visible device is not enough: cupy JIT-compiles kernels at first use and
    # needs the CUDA headers. Run a tiny real compute so a half-installed cupy
    # (device visible, no CTK headers) is reported unavailable, not faked.
    try:
        import numpy as _np
        probe = cupy.arange(4, dtype=cupy.float64)
        result = float((probe * 2.0).sum().get())
        if result != float((_np.arange(4) * 2.0).sum()):
            return False, "", {"cupy_smoke_mismatch": result}
    except Exception as exc:  # noqa: BLE001 - device visible but kernels won't run
        return False, "", {"cupy_compute_error": str(exc)[:200]}
    return True, device, {"cupy_version": getattr(cupy, "__version__", "?"), "devices": count}


def _probe_torch() -> tuple[bool, str, dict[str, Any]]:
    try:
        import torch  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return False, "", {"torch_import_error": str(exc)[:200]}
    try:
        if not torch.cuda.is_available():
            return False, "", {"torch_cuda_available": False}
        return True, torch.cuda.get_device_name(0), {"torch_version": torch.__version__}
    except Exception as exc:  # noqa: BLE001
        return False, "", {"torch_runtime_error": str(exc)[:200]}


def _probe_numba() -> tuple[bool, str, dict[str, Any]]:
    try:
        from numba import cuda  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return False, "", {"numba_import_error": str(exc)[:200]}
    try:
        if not cuda.is_available():
            return False, "", {"numba_cuda_available": False}
        dev = cuda.get_current_device()
        name = getattr(dev, "name", b"")
        device = name.decode() if isinstance(name, (bytes, bytearray)) else str(name)
        return True, device, {}
    except Exception as exc:  # noqa: BLE001
        return False, "", {"numba_runtime_error": str(exc)[:200]}


def _detect(force_cpu: bool) -> GpuCapability:
    smi_name = _nvidia_smi_name()
    smi_present = smi_name is not None

    if force_cpu:
        return GpuCapability(
            False, "none", "",
            "disabled by STRATEGY_LAB_FORCE_CPU", CPU, smi_present,
            {"nvidia_smi_name": smi_name or ""},
        )

    detail: dict[str, Any] = {"nvidia_smi_name": smi_name or ""}
    # The current kernels consume a NumPy-compatible ``xp`` module. CuPy is the
    # only implemented device executor. Torch/Numba visibility is diagnostic
    # information only and must never make this runtime claim GPU execution.
    ok, device, info = _probe_cupy()
    detail.update(info)
    if ok:
        return GpuCapability(True, "cupy", device, "", CPU, smi_present, detail)
    for name, probe in (("torch", _probe_torch), ("numba", _probe_numba)):
        visible, visible_device, probe_info = probe()
        detail.update(probe_info)
        detail[f"{name}_visible_but_not_executor"] = bool(visible)
        if visible_device:
            detail[f"{name}_device"] = visible_device

    if smi_present:
        reason = (
            f"GPU detected by nvidia-smi ({smi_name or 'unknown'}) but no usable GPU compute "
            "executor is installed (the implemented executor is CuPy). Install "
            "`pip install cupy-cuda12x`, to enable the GPU path."
        )
    else:
        reason = "no NVIDIA GPU detected (nvidia-smi absent) and no GPU compute backend installed."
    return GpuCapability(False, "none", "", reason, CPU, smi_present, detail)


@lru_cache(maxsize=1)
def detect_gpu() -> GpuCapability:
    """Probe for a usable GPU backend (cached). STRATEGY_LAB_FORCE_CPU=1 forces CPU."""
    force_cpu = os.getenv("STRATEGY_LAB_FORCE_CPU", "").strip().lower() in {"1", "true", "yes"}
    return _detect(force_cpu)


def reset_detection_cache() -> None:
    """Clear the cached probe (used by tests that monkeypatch the environment)."""
    detect_gpu.cache_clear()


def gpu_available() -> bool:
    return detect_gpu().gpu_available


def doctor() -> dict[str, Any]:
    """Operator-facing capability report."""
    cap = detect_gpu()
    return cap.to_dict()


@dataclass(frozen=True)
class BackendResolution:
    """How a requested backend was resolved for an actual run."""

    requested_backend: str
    effective_backend: str
    gpu_available: bool
    backend_name: str
    device_name: str
    fallback_reason: str
    error: str = ""

    def ok(self) -> bool:
        return not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_backend": self.requested_backend,
            "effective_backend": self.effective_backend,
            "gpu_available": self.gpu_available,
            "backend_name": self.backend_name,
            "device_name": self.device_name,
            "fallback_reason": self.fallback_reason,
            "error": self.error,
        }


def resolve_backend(requested: str) -> BackendResolution:
    """Resolve cpu/gpu/auto into the effective backend, honestly.

    - cpu  -> always cpu.
    - gpu  -> gpu if a GPU backend is available, else an ERROR (never silent CPU).
    - auto -> gpu if available, else cpu with an explicit fallback_reason.
    """
    req = str(requested or CPU).strip().lower()
    cap = detect_gpu()

    if req == CPU:
        return BackendResolution(CPU, CPU, cap.gpu_available, "none", "", "")
    if req == GPU:
        if cap.gpu_available:
            return BackendResolution(GPU, GPU, True, cap.backend_name, cap.device_name, "")
        return BackendResolution(
            GPU, CPU, False, "none", "",
            cap.reason_if_unavailable,
            error=f"backend 'gpu' requested but unavailable: {cap.reason_if_unavailable}",
        )
    if req == AUTO:
        if cap.gpu_available:
            return BackendResolution(AUTO, GPU, True, cap.backend_name, cap.device_name, "")
        return BackendResolution(
            AUTO, CPU, False, "none", "",
            f"auto -> cpu fallback: {cap.reason_if_unavailable}",
        )
    return BackendResolution(
        req, CPU, cap.gpu_available, "none", "",
        f"unknown backend '{requested}' -> cpu",
        error=f"unknown backend '{requested}'",
    )


def array_module(backend_name: str):
    """Return the numpy-compatible array module for the resolved GPU backend.

    CuPy for the implemented GPU backend. A different backend token is an error:
    silently returning NumPy here would report CPU work as GPU acceleration.
    """
    if backend_name == "cupy":
        try:
            import cupy  # type: ignore
            return cupy
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(f"GPU backend '{backend_name}' has no implemented array executor")
