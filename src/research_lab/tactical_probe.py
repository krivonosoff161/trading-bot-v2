# -*- coding: utf-8 -*-
"""Tactical-probe bucket — characterize the thin (sub-power) net-positive setups the validator
structurally rejects, WITHOUT calling them edge (research-only).

The honest validator has no statistical power below n=POWER_FLOOR (10), so every 1..9-trade setup is
rejected regardless of its net. The question (owner's point 8) is whether that n-gate is a blind
"meat grinder" shredding a coherent pool of good setups, or correctly filtering luck.

This module does NOT relabel or create a second brain. It reads the existing derived setup-lifecycle
rows (family/timeframe/n_trades/avg_net/regime/tactical_status) and characterizes the thin pool:

  * what conditions (family / timeframe / regime / n) produced the net-positive thin setups;
  * per-family positive-vs-negative skew, which separates a coherent thin phenomenon (positives far
    outnumber negatives) from coin-flip noise (~50/50) from a correctly-rejected family (mostly negative).

A family with a strong positive skew is flagged ``thin_positive_skew`` = "deserves a forward
characterization / probe", NEVER edge and NEVER paper-ready. The output answers the meat-grinder
question with numbers; it promotes nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any

from src.research_lab.setup_lifecycle import POWER_FLOOR, derive_setup_lifecycle

MIN_FAMILY_N = 10      # below this a family's skew verdict is "insufficient"
SKEW_POSITIVE = 0.60   # pos / (pos+neg) at/above this = coherent thin-positive skew
SKEW_NEGATIVE = 0.40   # at/below this = the gate is correctly rejecting this family


def _thin(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if 1 <= int(r.get("n_trades") or 0) < POWER_FLOOR]


def _tally(rows: list[dict[str, Any]], key: str, *, top: int | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "(none)")
        out[k] = out.get(k, 0) + 1
    ordered = dict(sorted(out.items(), key=lambda kv: -kv[1]))
    return dict(list(ordered.items())[:top]) if top else ordered


def _family_skew(thin: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-family positive-vs-negative split with a research-only verdict (never promotable)."""
    fams = sorted({str(r.get("family") or "") for r in thin})
    out: list[dict[str, Any]] = []
    for fam in fams:
        fam_rows = [r for r in thin if str(r.get("family") or "") == fam]
        pos = [r for r in fam_rows if float(r.get("avg_net_pct") or 0.0) > 0]
        neg = [r for r in fam_rows if float(r.get("avg_net_pct") or 0.0) <= 0]
        total = len(pos) + len(neg)
        rate = round(len(pos) / total, 4) if total else 0.0
        if total < MIN_FAMILY_N:
            verdict = "insufficient"
        elif rate >= SKEW_POSITIVE:
            verdict = "thin_positive_skew"      # coherent thin phenomenon -> worth a probe, NOT edge
        elif rate <= SKEW_NEGATIVE:
            verdict = "gate_correct_reject"      # mostly negative -> the n-gate is right to cut
        else:
            verdict = "noise_consistent"         # ~coin flip -> gate neutral / correct on average
        out.append({"family": fam, "n_pos": len(pos), "n_neg": len(neg), "pos_rate": rate,
                    "verdict": verdict})
    return sorted(out, key=lambda d: -(d["n_pos"] + d["n_neg"]))


def build_tactical_probe(private_root: Path) -> dict[str, Any]:
    rows = derive_setup_lifecycle(Path(private_root))
    thin = _thin(rows)
    pos = [r for r in thin if float(r.get("avg_net_pct") or 0.0) > 0]
    neg = [r for r in thin if float(r.get("avg_net_pct") or 0.0) <= 0]
    nets = sorted(float(r.get("avg_net_pct") or 0.0) for r in pos)
    skew = _family_skew(thin)
    probe_families = [s["family"] for s in skew if s["verdict"] == "thin_positive_skew"]
    overall_rate = round(len(pos) / len(thin), 4) if thin else 0.0
    return {
        "power_floor": POWER_FLOOR,
        "thin_total": len(thin), "thin_positive": len(pos), "thin_negative": len(neg),
        "overall_positive_rate": overall_rate,
        "net_positive_stats": {"min": round(nets[0], 4) if nets else 0.0,
                               "median": round(median(nets), 4) if nets else 0.0,
                               "max": round(nets[-1], 4) if nets else 0.0},
        "positive_by_family": _tally(pos, "family"),
        "positive_by_timeframe": _tally(pos, "timeframe"),
        "positive_by_regime": _tally(pos, "regime_bucket", top=6),
        "positive_by_n_trades": dict(sorted(_tally(pos, "n_trades").items(),
                                            key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0)),
        "family_skew": skew,
        "probe_families": probe_families,
        "meat_grinder_verdict": _verdict(overall_rate, probe_families),
        "paper_forward_ready": False,
    }


def _verdict(overall_rate: float, probe_families: list[str]) -> str:
    """One-line research answer to 'is the n-gate a blind meat grinder?' (never a promotion)."""
    coin = abs(overall_rate - 0.5) <= 0.05
    if probe_families:
        base = ("n-gate is NOT blindly correct: aggregate thin-positive rate ~coin-flip, "
                if coin else "aggregate thin-positive rate skewed, ")
        return base + ("but " + ", ".join(probe_families) +
                       " show a coherent thin-positive skew -> tactical probe (forward watch), NOT edge")
    return ("n-gate looks structurally correct: thin pool is ~coin-flip / negative with no coherent "
            "positive-skew family -> rejecting thin setups filters luck, not edge")


def write_snapshot(private_root: Path, probe: dict[str, Any] | None = None) -> Path:
    probe = probe if probe is not None else build_tactical_probe(private_root)
    out_dir = Path(private_root) / "state" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "tactical_probe.json"
    payload = {"schema": "tactical_probe.v1",
               "disclaimer": "Research-only characterization of sub-power (n<POWER_FLOOR) net-positive "
                             "setups the validator structurally rejects. thin_positive_skew = 'deserves a "
                             "forward probe', NEVER edge, NEVER paper-ready. Promotes nothing.",
               "probe": probe}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.research_lab.paths import DEFAULT_PRIVATE_ROOT
    ap = argparse.ArgumentParser(description="Tactical-probe characterization of thin setups (research-only).")
    ap.add_argument("--private-root", default=os.getenv("TRADING_BOT_RESEARCH_ROOT", str(DEFAULT_PRIVATE_ROOT)))
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args()
    probe = build_tactical_probe(Path(args.private_root))
    print(json.dumps(probe, ensure_ascii=False, indent=2))
    if args.snapshot:
        print("snapshot:", write_snapshot(Path(args.private_root), probe))


if __name__ == "__main__":
    main()
