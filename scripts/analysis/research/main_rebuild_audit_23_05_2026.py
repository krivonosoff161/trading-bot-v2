"""Per-regime correctness audit (Phase 1: numbers) — 23.05.2026.

Trader wants the whole-system check back: for each regime the classifier produced,
is the regime TYPE right, the DIRECTION right, the TP/SL right? Phase 1 quantifies what
is measurable, per classified regime:
  - how many events traded vs SKIPPED (and why) -> recall / what we never trade (grind!)
  - direction accuracy: model side == actual move direction
  - outcome mix, MFE available vs captured, entry lag
Phase 2 (separate) renders annotated charts of the wrong/ambiguous cases + corrected overlay.

Reuses main_rebuild_replay pipeline read-only.
Run: python scripts/analysis/research/main_rebuild_audit_23_05_2026.py
"""

from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import main_rebuild_replay_23_05_2026 as mrr

REF_EXIT = "scaled_tp_100"   # one row per traded event
REF_ENTRY = "mid"


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float)) and math.isfinite(x)]
    return sum(xs) / len(xs) if xs else float("nan")


def main() -> int:
    _, candle_sets, _, events, s, e = mrr.base.load_replay()
    rows = mrr.build_rows(events, candle_sets, s, e)

    # one record per event: traded (entry=mid, exit=ref) OR the skip row
    per_event = {}
    for r in rows:
        key = (r["symbol"], r["ts"])
        if r.get("skip_reason"):
            per_event.setdefault(key, r)  # skip row (single)
        elif r.get("entry_mode") == REF_ENTRY and r.get("base_exit") == REF_EXIT:
            per_event[key] = r  # traded row overrides skip if both (shouldn't)

    by_regime = defaultdict(list)
    for r in per_event.values():
        by_regime[r.get("cell", "?")].append(r)

    print(f"\n=== PER-REGIME AUDIT (window {mrr.base.iso_from_ms(s)[:10]}..{mrr.base.iso_from_ms(e)[:10]}) ===")
    print(f"events total={len(per_event)}\n")

    for regime in sorted(by_regime):
        rs = by_regime[regime]
        traded = [r for r in rs if not r.get("skip_reason")]
        skipped = [r for r in rs if r.get("skip_reason")]
        skip_reasons = Counter(r["skip_reason"].split(":")[0] for r in skipped)
        print(f"### {regime}  (events={len(rs)}, traded={len(traded)}, skipped={len(skipped)})")
        if skip_reasons:
            print(f"   skip reasons: {dict(skip_reasons)}")
        if traded:
            # direction accuracy: model_side vs actual event direction
            matched = [r for r in traded if r.get("model_side") and r.get("event_direction")
                       and r["model_side"] == r["event_direction"]]
            outcomes = Counter((r.get("new_exit") or {}).get("outcome") for r in traded)
            mfe = mean([(r.get("diagnostics") or {}).get("available_from_impulse_pct") for r in traded])
            lag = mean([(r.get("diagnostics") or {}).get("entry_lag_pct") for r in traded])
            net = mean([(r.get("new_exit") or {}).get("net_pct") for r in traded])
            cap = mean([r.get("new_capture_pct") for r in traded])
            print(f"   direction match: {len(matched)}/{len(traded)} = {len(matched)/len(traded)*100:.0f}%")
            print(f"   net={net:+.3f}%  MFE avail={mfe:.2f}%  capture={cap:.0f}%  entry lag={lag:.2f}%")
            print(f"   outcomes: {dict(outcomes)}")
        print()

    # grind hunt: events the model skipped as grind_watch (the long smooth trends we never trade)
    grind = [r for r in per_event.values() if (r.get("skip_reason") or "").startswith("grind")]
    print(f"=== GRIND (trend_grind_watch — classified but NEVER traded): {len(grind)} events ===")
    gsym = Counter(r["symbol"] for r in grind)
    print(f"   by pair (top): {dict(gsym.most_common(10))}")
    print("   -> these are candidate long smooth moves for engine #3 (Phase 2 will chart them)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
