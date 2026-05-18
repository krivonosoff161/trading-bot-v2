from __future__ import annotations

import csv
import gzip
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]

LIVE_SIGNALS = ROOT / "logs" / "pump" / "pump_signals.jsonl"
LIVE_LABELS = ROOT / "logs" / "pump" / "pump_labels.jsonl"
LIVE_LOG = ROOT / "logs" / "pump" / "ws_pump_orchestrator.log"
TAPE_ROOT = Path("E:/trading-data/ticks")

ARCHIVE_SIGNALS = ROOT / "logs_archive" / "09.05.2026" / "pump" / "pump_signals.jsonl"
ARCHIVE_LABELS = ROOT / "logs_archive" / "09.05.2026" / "pump" / "pump_labels.jsonl"

OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_PATH = OUT_DIR / "pump_block3_report.md"
DATA_PATH = OUT_DIR / "pump_block3_dataset.json"

COUNTED = {"TP", "SL"}

LOG_CLOSE_RE = re.compile(
    r"^\[(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})\] CLOSE \| "
    r"(?P<sym>[A-Z0-9\-]+)\s+\| (?P<reason>TP|SL) \| pnl=(?P<pnl>[+\-]?\d+(?:\.\d+)?)% "
    r"(?:\| mfe=(?P<mfe>[+\-]?\d+(?:\.\d+)?)% mae=(?P<mae>[+\-]?\d+(?:\.\d+)?)% )?"
    r"\| hold=(?P<hold>\d+)m \| section=(?P<section>[a-z]+)"
)
LOG_BAN_UNTIL_RE = re.compile(r"banned until (?P<date>\d{4}-\d{2}-\d{2}) 00:00 UTC")

WINDOW_BEFORE_MS = 5 * 60 * 1000
WINDOW_AFTER_MS = 60 * 1000
BABY_TAPE_VETO_RULE = "pre_buy_ratio<0.50 && pre_cvd<0 && post_buy_ratio<0.40 && post_cvd<0"


@dataclass(slots=True)
class SimConfig:
    zero_pairs: set[str]
    half_pairs: set[str]
    capped_pairs: dict[str, int]
    daily_ban_pairs: dict[str, int]
    daily_ban_all: int | None = None
    exclude_signal_ids: set[str] | None = None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def load_joined() -> list[dict[str, Any]]:
    signals: dict[str, dict[str, Any]] = {}
    for path, source in ((ARCHIVE_SIGNALS, "archive"), (LIVE_SIGNALS, "live")):
        for row in read_jsonl(path):
            signal_id = row.get("signal_id")
            if signal_id:
                entry = dict(row)
                entry["_source"] = source
                signals[signal_id] = entry

    rows: list[dict[str, Any]] = []
    for path, source in ((ARCHIVE_LABELS, "archive"), (LIVE_LABELS, "live")):
        for label in read_jsonl(path):
            signal_id = label.get("signal_id")
            sig = signals.get(signal_id)
            if not sig:
                continue
            opened_at = parse_dt(label["opened_at"])
            closed_at = parse_dt(label["closed_at"])
            reason = str(label.get("exit_reason") or "").upper()
            rows.append(
                {
                    "signal_id": signal_id,
                    "source": source,
                    "sym": str(label["sym"]),
                    "pair": str(label["sym"]),
                    "direction": sig.get("direction"),
                    "section": str(sig.get("section") or "main"),
                    "opened_at": opened_at,
                    "closed_at": closed_at,
                    "trade_date": opened_at.strftime("%Y-%m-%d"),
                    "hour_utc": opened_at.hour,
                    "reason": reason,
                    "counted": reason in COUNTED,
                    "entry_price": safe_float(label.get("entry_price")),
                    "exit_price": safe_float(label.get("exit_price")),
                    "net_pnl_pct": safe_float(label.get("net_pnl_pct")),
                    "gross_pnl_pct": safe_float(label.get("gross_pnl_pct")),
                    "hold_min": safe_float(label.get("hold_min")),
                    "vol_ratio": safe_float(sig.get("vol_ratio")),
                    "pct_move": safe_float(sig.get("pct_move")),
                    "dollar_vol": safe_float(sig.get("dollar_vol")),
                    "atr": safe_float(sig.get("atr") or sig.get("atr_1h")),
                    "filters_passed": sig.get("filters_passed"),
                    "mfe_pct": safe_float(label.get("mfe_pct")),
                    "mae_pct": safe_float(label.get("mae_pct")),
                }
            )
    rows.sort(key=lambda row: row["opened_at"])
    return rows


def parse_live_log_rows(start_date: datetime) -> list[dict[str, Any]]:
    current_date = start_date.date()
    prev_time: tuple[int, int, int] | None = None
    parsed: list[dict[str, Any]] = []

    with LIVE_LOG.open("r", encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            raw = raw.strip()
            ban_match = LOG_BAN_UNTIL_RE.search(raw)
            if ban_match:
                current_date = (
                    datetime.fromisoformat(ban_match.group("date")).date() - timedelta(days=1)
                )
            match = LOG_CLOSE_RE.match(raw)
            if not match:
                continue
            hh = int(match.group("h"))
            mm = int(match.group("m"))
            ss = int(match.group("s"))
            cur_time = (hh, mm, ss)
            if prev_time is not None and cur_time < prev_time:
                current_date += timedelta(days=1)
            prev_time = cur_time

            dt = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                hh,
                mm,
                ss,
                tzinfo=timezone.utc,
            )
            parsed.append(
                {
                    "dt": dt,
                    "sym": match.group("sym"),
                    "reason": match.group("reason"),
                    "hold_min": float(match.group("hold")),
                    "section": match.group("section"),
                    "pnl": float(match.group("pnl")),
                    "mfe_pct": safe_float(match.group("mfe")),
                    "mae_pct": safe_float(match.group("mae")),
                }
            )
    return parsed


def match_log_rows(live_rows: list[dict[str, Any]], log_rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in log_rows:
        key = (item["sym"], item["dt"].strftime("%Y-%m-%d"), item["reason"])
        candidates[key].append(item)

    matched = 0
    ambiguous = 0
    unmatched = 0
    for row in live_rows:
        if math.isfinite(row["mfe_pct"]):
            continue
        trade_date = row["closed_at"].strftime("%Y-%m-%d")
        key = (row["sym"], trade_date, row["reason"])
        pool = candidates.get(key, [])
        viable: list[tuple[tuple[float, float, float], int]] = []
        for idx, item in enumerate(pool):
            pnl_gap = abs(item["pnl"] - row["net_pnl_pct"])
            hold_gap = abs(item["hold_min"] - row["hold_min"])
            dt_gap = abs((item["dt"] - row["closed_at"]).total_seconds())
            if pnl_gap < 0.05 and hold_gap <= 1.0:
                viable.append(((pnl_gap, hold_gap, dt_gap), idx))
        if not viable:
            unmatched += 1
            continue
        viable.sort()
        if len(viable) >= 2 and viable[0][0] == viable[1][0]:
            ambiguous += 1
            continue
        _, best_idx = viable[0]
        item = pool.pop(best_idx)
        row["mfe_pct"] = item["mfe_pct"]
        row["mae_pct"] = item["mae_pct"]
        matched += 1
    return matched, ambiguous, unmatched


def attach_live_log_metrics(rows: list[dict[str, Any]]) -> tuple[int, int, int, int, str]:
    live_rows = [row for row in rows if row["source"] == "live"]
    if not live_rows:
        return 0, 0, 0, 0, "n/a"

    missing_rows = [row for row in live_rows if not math.isfinite(row["mfe_pct"])]
    if not missing_rows:
        return 0, len(live_rows), 0, 0, "label_only"

    earliest = min(row["closed_at"] for row in missing_rows).date()
    best_matched = -1
    best_start = None
    best_log_rows = None
    best_ambiguous = 0
    best_unmatched = 0

    for delta in range(0, 12):
        start_dt = datetime.combine(earliest - timedelta(days=delta), datetime.min.time(), tzinfo=timezone.utc)
        trial_rows = parse_live_log_rows(start_dt)
        scratch = [dict(row) for row in missing_rows]
        matched, ambiguous, unmatched = match_log_rows(scratch, [dict(item) for item in trial_rows])
        if matched > best_matched:
            best_matched = matched
            best_start = start_dt
            best_log_rows = trial_rows
            best_ambiguous = ambiguous
            best_unmatched = unmatched

    if best_log_rows is None or best_start is None:
        return 0, len(live_rows), 0, len(missing_rows), "n/a"

    matched, ambiguous, unmatched = match_log_rows(missing_rows, best_log_rows)
    return matched, len(live_rows), ambiguous, unmatched, best_start.strftime("%Y-%m-%d")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counted = [row for row in rows if row["counted"]]
    wins = sum(1 for row in counted if row["reason"] == "TP")
    losses = sum(1 for row in counted if row["reason"] == "SL")
    net = sum(row["net_pnl_pct"] for row in counted if math.isfinite(row["net_pnl_pct"]))
    return {
        "n": len(counted),
        "wins": wins,
        "losses": losses,
        "wr": wins / len(counted) * 100 if counted else float("nan"),
        "net_pct": net,
        "avg_net_pct": net / len(counted) if counted else float("nan"),
    }


def format_summary(label: str, summary: dict[str, Any]) -> str:
    wr = "n/a" if not math.isfinite(summary["wr"]) else f"{summary['wr']:.1f}%"
    avg = "n/a" if not math.isfinite(summary["avg_net_pct"]) else f"{summary['avg_net_pct']:+.2f}%"
    return f"- {label}: n={summary['n']}, WR={wr}, net={summary['net_pct']:+.2f}%, avg={avg}"


def apply_sim(rows: list[dict[str, Any]], cfg: SimConfig) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    day_pair_count: dict[tuple[str, str], int] = defaultdict(int)
    day_pair_sl_streak: dict[tuple[str, str], int] = defaultdict(int)

    for row in sorted(rows, key=lambda item: item["opened_at"]):
        if cfg.exclude_signal_ids and row["signal_id"] in cfg.exclude_signal_ids:
            continue
        pair = row["pair"]
        day = row["trade_date"]
        pair_day = (pair, day)

        if pair in cfg.zero_pairs:
            continue

        max_trades = cfg.capped_pairs.get(pair)
        if max_trades is not None and day_pair_count[pair_day] >= max_trades:
            continue

        threshold = cfg.daily_ban_pairs.get(pair)
        if threshold is None:
            threshold = cfg.daily_ban_all
        if threshold is not None and day_pair_sl_streak[pair_day] >= threshold:
            continue

        scaled = dict(row)
        if pair in cfg.half_pairs and math.isfinite(scaled["net_pnl_pct"]):
            scaled["net_pnl_pct"] *= 0.5

        out.append(scaled)
        day_pair_count[pair_day] += 1
        if scaled["reason"] == "SL":
            day_pair_sl_streak[pair_day] += 1
        elif scaled["reason"] == "TP":
            day_pair_sl_streak[pair_day] = 0
    return out


def pair_table(rows: list[dict[str, Any]], limit: int = 12) -> list[str]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["counted"]:
            groups[row["pair"]].append(row)
    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for pair, pair_rows in groups.items():
        summary = summarize(pair_rows)
        ranked.append((summary["net_pct"], -summary["n"], pair, summary))
    ranked.sort()

    lines = [
        "| Pair | n | WR | net | avg |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for _, _, pair, summary in ranked[:limit]:
        wr = "n/a" if not math.isfinite(summary["wr"]) else f"{summary['wr']:.1f}%"
        avg = "n/a" if not math.isfinite(summary["avg_net_pct"]) else f"{summary['avg_net_pct']:+.2f}%"
        lines.append(f"| {pair} | {summary['n']} | {wr} | {summary['net_pct']:+.2f}% | {avg} |")
    return lines


def live_breakeven_candidates(rows: list[dict[str, Any]]) -> list[str]:
    live = [row for row in rows if row["source"] == "live" and row["reason"] == "SL" and math.isfinite(row["mfe_pct"])]
    buckets = [
        ("mfe>=1.0%", [row for row in live if row["mfe_pct"] >= 1.0]),
        ("mfe>=0.8%", [row for row in live if row["mfe_pct"] >= 0.8]),
    ]
    return [format_summary(label, summarize(bucket)) for label, bucket in buckets]


def load_tape_rows(sym: str, date_str: str) -> list[dict[str, Any]]:
    for path in (TAPE_ROOT / sym / f"{date_str}.csv.gz", TAPE_ROOT / sym / f"{date_str}.csv"):
        if path.exists():
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as fh:
                return list(csv.DictReader(fh))
    return []


def calc_tape_metrics(tape_rows: list[dict[str, Any]], entry_ts_ms: int) -> dict[str, Any]:
    pre_buy = 0.0
    pre_sell = 0.0
    pre_ticks = 0
    post_buy = 0.0
    post_sell = 0.0
    post_ticks = 0

    for row in tape_rows:
        try:
            ts_ms = int(row["ts_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        side = row.get("side")
        if side not in {"buy", "sell"}:
            continue
        try:
            size = float(row.get("size") or 0.0)
        except (TypeError, ValueError):
            continue

        if entry_ts_ms - WINDOW_BEFORE_MS <= ts_ms <= entry_ts_ms:
            pre_ticks += 1
            if side == "buy":
                pre_buy += size
            else:
                pre_sell += size
        elif entry_ts_ms < ts_ms <= entry_ts_ms + WINDOW_AFTER_MS:
            post_ticks += 1
            if side == "buy":
                post_buy += size
            else:
                post_sell += size

    pre_total = pre_buy + pre_sell
    post_total = post_buy + post_sell
    return {
        "pre_buy_ratio": pre_buy / pre_total if pre_total > 0 else float("nan"),
        "pre_cvd": pre_buy - pre_sell,
        "pre_total": pre_total,
        "pre_ticks": pre_ticks,
        "post_buy_ratio": post_buy / post_total if post_total > 0 else float("nan"),
        "post_cvd": post_buy - post_sell,
        "post_total": post_total,
        "post_ticks": post_ticks,
    }


def build_current_tape_slice(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    analyzed: list[dict[str, Any]] = []
    present_no_window: list[dict[str, Any]] = []
    for row in rows:
        date_str = row["opened_at"].strftime("%Y-%m-%d")
        key = (row["sym"], date_str)
        if key not in cache:
            cache[key] = load_tape_rows(*key)
        tape_rows = cache[key]
        if not tape_rows:
            continue
        entry_ts_ms = int(row["opened_at"].timestamp() * 1000)
        metrics = calc_tape_metrics(tape_rows, entry_ts_ms)
        enriched = dict(row)
        enriched.update(metrics)
        if metrics["pre_ticks"] > 0:
            analyzed.append(enriched)
        else:
            present_no_window.append(enriched)
    return analyzed, present_no_window


def fmt_ratio(value: float) -> str:
    return "n/a" if not math.isfinite(value) else f"{value:.3f}"


def baby_tape_lines(tape_rows: list[dict[str, Any]], missing_window_rows: list[dict[str, Any]]) -> tuple[list[str], set[str]]:
    baby_rows = [row for row in tape_rows if row["pair"] == "BABY-USDT-SWAP"]
    missing_rows = [row for row in missing_window_rows if row["pair"] == "BABY-USDT-SWAP"]
    veto_ids: set[str] = set()
    lines = [
        f"- Rule tested: `{BABY_TAPE_VETO_RULE}`",
        f"- Covered BABY trades with usable tape: {len(baby_rows)}",
    ]
    for row in baby_rows:
        veto = (
            math.isfinite(row["pre_buy_ratio"])
            and math.isfinite(row["post_buy_ratio"])
            and row["pre_buy_ratio"] < 0.50
            and row["pre_cvd"] < 0
            and row["post_buy_ratio"] < 0.40
            and row["post_cvd"] < 0
        )
        if veto:
            veto_ids.add(row["signal_id"])
        lines.append(
            "- "
            f"{row['opened_at'].strftime('%Y-%m-%d %H:%M')} {row['reason']} net={row['net_pnl_pct']:+.2f}% "
            f"pre_buy={fmt_ratio(row['pre_buy_ratio'])} pre_cvd={row['pre_cvd']:+.0f} "
            f"post_buy={fmt_ratio(row['post_buy_ratio'])} post_cvd={row['post_cvd']:+.0f} "
            f"-> {'VETO' if veto else 'KEEP'}"
        )
    for row in missing_rows:
        lines.append(
            "- "
            f"{row['opened_at'].strftime('%Y-%m-%d %H:%M')} {row['reason']} file exists but no ticks in entry window "
            f"(day coverage gap; file ended early)"
        )
    return lines, veto_ids


def tape_pair_coverage_lines(rows: list[dict[str, Any]], tape_rows: list[dict[str, Any]], missing_window_rows: list[dict[str, Any]]) -> list[str]:
    covered_ids = {row["signal_id"] for row in tape_rows}
    missing_window_ids = {row["signal_id"] for row in missing_window_rows}
    pairs = ["APR-USDT-SWAP", "RIVER-USDT-SWAP", "LAB-USDT-SWAP", "BABY-USDT-SWAP", "BSB-USDT-SWAP", "BILL-USDT-SWAP"]
    lines = ["| Pair | current_n | usable_tape | file_present_no_window | note |", "| --- | ---: | ---: | ---: | --- |"]
    for pair in pairs:
        pair_rows = [row for row in rows if row["pair"] == pair]
        usable = sum(1 for row in pair_rows if row["signal_id"] in covered_ids)
        partial = sum(1 for row in pair_rows if row["signal_id"] in missing_window_ids)
        if pair in {"APR-USDT-SWAP", "RIVER-USDT-SWAP", "LAB-USDT-SWAP"}:
            note = "no tape files on disk"
        elif partial:
            note = "file exists but day coverage incomplete"
        else:
            note = "usable"
        lines.append(f"| {pair} | {len(pair_rows)} | {usable} | {partial} | {note} |")
    return lines


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_joined()
    matched, live_total, ambiguous, unmatched, matched_start_date = attach_live_log_metrics(rows)

    current_rows = [row for row in rows if row["opened_at"] >= datetime(2026, 5, 16, tzinfo=timezone.utc)]
    full_rows = [row for row in rows if row["opened_at"] >= datetime(2026, 5, 3, tzinfo=timezone.utc)]
    current_tape_rows, current_tape_partial = build_current_tape_slice(current_rows)
    baby_tape_report, baby_veto_ids = baby_tape_lines(current_tape_rows, current_tape_partial)

    sims = {
        "Sim0 current baseline": apply_sim(
            current_rows,
            SimConfig(set(), set(), {}, {}, None, None),
        ),
        "Sim1 BABY off": apply_sim(
            current_rows,
            SimConfig({"BABY-USDT-SWAP"}, set(), {}, {}, None, None),
        ),
        "Sim2 BABY+RIVER off": apply_sim(
            current_rows,
            SimConfig({"BABY-USDT-SWAP", "RIVER-USDT-SWAP"}, set(), {}, {}, None, None),
        ),
        "Sim3 APR half": apply_sim(
            current_rows,
            SimConfig(set(), {"APR-USDT-SWAP"}, {}, {}, None, None),
        ),
        "Sim4 BSB half": apply_sim(
            current_rows,
            SimConfig(set(), {"BSB-USDT-SWAP"}, {}, {}, None, None),
        ),
        "Sim5 BILL cap2 ban2": apply_sim(
            current_rows,
            SimConfig(set(), set(), {"BILL-USDT-SWAP": 2}, {"BILL-USDT-SWAP": 2}, None, None),
        ),
        "Sim6 all overrides": apply_sim(
            current_rows,
            SimConfig(
                {"BABY-USDT-SWAP", "RIVER-USDT-SWAP"},
                {"APR-USDT-SWAP", "BSB-USDT-SWAP"},
                {"BILL-USDT-SWAP": 2},
                {"BILL-USDT-SWAP": 2},
                None,
                None,
            ),
        ),
        "Sim7 ban_after_sl_streak=2 all pairs": apply_sim(
            current_rows,
            SimConfig(set(), set(), {}, {}, 2, None),
        ),
        "Sim8 hard blocks APR/RIVER/LAB + BABY tape veto": apply_sim(
            current_rows,
            SimConfig(
                {"APR-USDT-SWAP", "RIVER-USDT-SWAP", "LAB-USDT-SWAP"},
                set(),
                {},
                {},
                None,
                baby_veto_ids,
            ),
        ),
        "Sim9 deployable hybrid blocks + BSB half + BILL cap2": apply_sim(
            current_rows,
            SimConfig(
                {"APR-USDT-SWAP", "RIVER-USDT-SWAP", "LAB-USDT-SWAP", "BABY-USDT-SWAP"},
                {"BSB-USDT-SWAP"},
                {"BILL-USDT-SWAP": 2},
                {"BILL-USDT-SWAP": 2},
                None,
                None,
            ),
        ),
    }

    lines: list[str] = []
    lines.append("# Block 3 Pump Analysis")
    lines.append("")
    lines.append(format_summary("Current baseline 2026-05-16..2026-05-18", summarize(current_rows)))
    lines.append(format_summary("Expanded sample 2026-05-03..2026-05-18", summarize(full_rows)))
    direct_live_mfe = sum(1 for row in rows if row["source"] == "live" and math.isfinite(row["mfe_pct"]))
    current_direct_mfe = sum(1 for row in current_rows if math.isfinite(row["mfe_pct"]))
    lines.append(
        "- Live MFE/MAE coverage: "
        f"current_window={current_direct_mfe}/{len(current_rows)} from labels; "
        f"full_live direct_or_log={direct_live_mfe}/{live_total} direct labels plus log_backfill={matched}, "
        f"ambiguous={ambiguous}, unmatched={unmatched}, best_log_start={matched_start_date}."
    )
    lines.append("- Archive 2026-05-03..2026-05-09 does not have local orchestrator MFE log, so breakeven reinterpretation is only reliable on the live subset.")
    lines.append("")
    lines.append("## Pair Drag: Current")
    lines.extend(pair_table(current_rows, limit=10))
    lines.append("")
    lines.append("## Pair Drag: Expanded")
    lines.extend(pair_table(full_rows, limit=10))
    lines.append("")
    lines.append("## Breakeven Candidate Slice (live SL only)")
    lines.extend(live_breakeven_candidates(current_rows))
    lines.append("")
    lines.append("## Tape Coverage on Current Baseline")
    lines.extend(tape_pair_coverage_lines(current_rows, current_tape_rows, current_tape_partial))
    lines.append("")
    lines.append("## BABY Tape Slice")
    lines.extend(baby_tape_report)
    lines.append("")
    lines.append("## Sim0-Sim9 on Current Baseline")
    base_net = summarize(sims["Sim0 current baseline"])["net_pct"]
    for name, sim_rows in sims.items():
        summary = summarize(sim_rows)
        delta = summary["net_pct"] - base_net
        lines.append(format_summary(name, summary) + f" | delta_vs_base={delta:+.2f}pp")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    DATA_PATH.write_text(
        json.dumps(
            {
                "rows_total": len(rows),
                "current_rows": len(current_rows),
                "expanded_rows": len(full_rows),
                "live_log_matched": matched,
                "live_total": live_total,
                "live_log_ambiguous": ambiguous,
                "live_log_unmatched": unmatched,
                "matched_start_date": matched_start_date,
                "current_tape_rows": len(current_tape_rows),
                "current_tape_partial_rows": len(current_tape_partial),
                "baby_tape_veto_ids": sorted(baby_veto_ids),
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved {REPORT_PATH}")
    print(f"saved {DATA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
