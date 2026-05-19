from __future__ import annotations

import csv
import gzip
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
TICK_ROOT = Path(r"E:\trading-data\ticks")
OUT_DIR = ROOT / "scripts" / "analysis" / "research" / "output"
REPORT_PATH = OUT_DIR / "volatility_scalper_report.md"
AI_PATH = OUT_DIR / "volatility_scalper_AI.json"
EDEN_PATH = OUT_DIR / "volatility_scalper_EDEN.json"
PUMP_SIGNALS = ROOT / "logs" / "pump" / "pump_signals.jsonl"
PUMP_LABELS = ROOT / "logs" / "pump" / "pump_labels.jsonl"

PAIRS = {
    "AI-USDT-SWAP": ["2026-05-15", "2026-05-16", "2026-05-17", "2026-05-18", "2026-05-19"],
    "EDEN-USDT-SWAP": ["2026-05-17", "2026-05-18", "2026-05-19"],
}
HORIZONS = [1, 2, 3, 5, 10, 15]
EXPLOSION_THRESHOLD_PCT = 0.8
REVERSAL_THRESHOLD_PCT = 0.5


@dataclass(slots=True)
class Bar:
    minute_ms: int
    ts: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume_contracts: float
    buy_vol: float
    sell_vol: float
    delta: float
    cvd: float
    buy_ratio: float
    price_change_pct: float


@dataclass(slots=True)
class Explosion:
    ts: str
    date: str
    minute_ms: int
    direction: str
    size_pct: float
    buy_ratio: float
    volume_contracts: float
    pre_cvd_5m: float
    pre_buy_ratio_5m: float
    pre_vol_5m: float
    forward: dict[str, dict[str, float | bool | None]]
    next_opposite_min: int | None
    next_opposite_size_pct: float | None
    next_same_after_opposite_min: int | None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def tick_path(pair: str, date: str) -> Path | None:
    base = TICK_ROOT / pair
    for suffix in (".csv.gz", ".csv"):
        path = base / f"{date}{suffix}"
        if path.exists():
            return path
    return None


def open_tick_file(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def aggregate_bars(pair: str, date: str) -> list[Bar]:
    path = tick_path(pair, date)
    if path is None:
        return []

    minutes: dict[int, dict[str, Any]] = {}
    with open_tick_file(path) as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            side = (row.get("side") or "").lower()
            if side == "gap" or side not in {"buy", "sell"}:
                continue
            ts_ms = int(float(row["ts_ms"]))
            minute_ms = ts_ms - (ts_ms % 60000)
            price = safe_float(row.get("price"))
            size = safe_float(row.get("size"))
            if not math.isfinite(price) or not math.isfinite(size):
                continue
            bucket = minutes.get(minute_ms)
            if bucket is None:
                bucket = {
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0.0,
                    "buy_vol": 0.0,
                    "sell_vol": 0.0,
                }
                minutes[minute_ms] = bucket
            bucket["high"] = max(bucket["high"], price)
            bucket["low"] = min(bucket["low"], price)
            bucket["close"] = price
            bucket["volume"] += size
            if side == "buy":
                bucket["buy_vol"] += size
            else:
                bucket["sell_vol"] += size

    bars: list[Bar] = []
    cvd = 0.0
    for minute_ms in sorted(minutes):
        bucket = minutes[minute_ms]
        delta = bucket["buy_vol"] - bucket["sell_vol"]
        cvd += delta
        denom = bucket["buy_vol"] + bucket["sell_vol"]
        buy_ratio = bucket["buy_vol"] / denom if denom > 0 else float("nan")
        open_p = bucket["open"]
        close_p = bucket["close"]
        change = (close_p - open_p) / open_p * 100 if open_p > 0 else float("nan")
        bars.append(
            Bar(
                minute_ms=minute_ms,
                ts=iso_from_ms(minute_ms),
                date=date,
                open=open_p,
                high=bucket["high"],
                low=bucket["low"],
                close=close_p,
                volume_contracts=bucket["volume"],
                buy_vol=bucket["buy_vol"],
                sell_vol=bucket["sell_vol"],
                delta=delta,
                cvd=cvd,
                buy_ratio=buy_ratio,
                price_change_pct=change,
            )
        )
    return bars


def directional_return(entry: float, price: float, direction: str) -> float:
    if entry <= 0 or price <= 0:
        return float("nan")
    if direction == "long":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def prior_slice(bars: list[Bar], idx: int, lookback: int = 5) -> list[Bar]:
    return bars[max(0, idx - lookback):idx]


def detect_explosions(bars: list[Bar]) -> list[Explosion]:
    by_minute = {bar.minute_ms: bar for bar in bars}
    raw_indices = [
        idx for idx, bar in enumerate(bars)
        if math.isfinite(bar.price_change_pct) and abs(bar.price_change_pct) >= EXPLOSION_THRESHOLD_PCT
    ]

    explosions: list[Explosion] = []
    for idx in raw_indices:
        bar = bars[idx]
        direction = "long" if bar.price_change_pct > 0 else "short"
        prev = prior_slice(bars, idx, 5)
        pre_delta = sum(item.delta for item in prev)
        pre_buy = sum(item.buy_vol for item in prev)
        pre_sell = sum(item.sell_vol for item in prev)
        pre_buy_ratio = pre_buy / (pre_buy + pre_sell) if pre_buy + pre_sell > 0 else float("nan")
        pre_vol = sum(item.volume_contracts for item in prev)

        forward: dict[str, dict[str, float | bool | None]] = {}
        for horizon in HORIZONS:
            future = by_minute.get(bar.minute_ms + horizon * 60000)
            if future is None:
                forward[str(horizon)] = {"return_pct": None, "continued": None, "reversed_gt_0p5": None}
                continue
            ret = directional_return(bar.close, future.close, direction)
            forward[str(horizon)] = {
                "return_pct": ret,
                "continued": ret > 0,
                "reversed_gt_0p5": ret <= -REVERSAL_THRESHOLD_PCT,
            }

        explosions.append(
            Explosion(
                ts=bar.ts,
                date=bar.date,
                minute_ms=bar.minute_ms,
                direction=direction,
                size_pct=bar.price_change_pct,
                buy_ratio=bar.buy_ratio,
                volume_contracts=bar.volume_contracts,
                pre_cvd_5m=pre_delta,
                pre_buy_ratio_5m=pre_buy_ratio,
                pre_vol_5m=pre_vol,
                forward=forward,
                next_opposite_min=None,
                next_opposite_size_pct=None,
                next_same_after_opposite_min=None,
            )
        )

    for i, exp in enumerate(explosions):
        opposite_idx = None
        for j in range(i + 1, len(explosions)):
            if explosions[j].direction != exp.direction:
                opposite_idx = j
                opposite = explosions[j]
                exp.next_opposite_min = int((opposite.minute_ms - exp.minute_ms) / 60000)
                exp.next_opposite_size_pct = opposite.size_pct
                break
        if opposite_idx is not None:
            for k in range(opposite_idx + 1, len(explosions)):
                if explosions[k].direction == exp.direction:
                    exp.next_same_after_opposite_min = int((explosions[k].minute_ms - exp.minute_ms) / 60000)
                    break
    return explosions


def avg(values: Iterable[float]) -> float:
    vals = [v for v in values if math.isfinite(v)]
    return sum(vals) / len(vals) if vals else float("nan")


def pct(part: int, total: int) -> float:
    return part / total * 100 if total else float("nan")


def summarize_forward(explosions: list[Explosion]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for horizon in HORIZONS:
        rows = [exp.forward[str(horizon)] for exp in explosions if exp.forward[str(horizon)]["return_pct"] is not None]
        returns = [safe_float(row["return_pct"]) for row in rows]
        continued = sum(1 for row in rows if row["continued"] is True)
        reversed_count = sum(1 for row in rows if row["reversed_gt_0p5"] is True)
        out[str(horizon)] = {
            "n": len(rows),
            "avg_forward_return_pct": avg(returns),
            "continued_pct": pct(continued, len(rows)),
            "reversed_gt_0p5_pct": pct(reversed_count, len(rows)),
        }
    return out


def summarize_by_day(explosions: list[Explosion]) -> list[dict[str, Any]]:
    by_day: dict[str, list[Explosion]] = defaultdict(list)
    for exp in explosions:
        by_day[exp.date].append(exp)
    rows = []
    for day, items in sorted(by_day.items()):
        rows.append(
            {
                "date": day,
                "total": len(items),
                "long": sum(1 for item in items if item.direction == "long"),
                "short": sum(1 for item in items if item.direction == "short"),
                "avg_abs_size_pct": avg(abs(item.size_pct) for item in items),
            }
        )
    return rows


def predictor_stats(bars: list[Bar], explosions: list[Explosion]) -> list[dict[str, Any]]:
    exp_by_minute = {exp.minute_ms: exp for exp in explosions}
    day_avg_vol: dict[str, float] = {}
    by_day: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        by_day[bar.date].append(bar)
    for date, items in by_day.items():
        day_avg_vol[date] = avg(item.volume_contracts for item in items)

    rows: dict[str, dict[str, int]] = defaultdict(lambda: {"present": 0, "present_correct": 0, "absent": 0, "absent_correct": 0})
    for idx, bar in enumerate(bars[:-1]):
        next_exp = exp_by_minute.get(bars[idx + 1].minute_ms)
        prev3 = prior_slice(bars, idx, 3)
        prev5 = prior_slice(bars, idx, 5)
        if not prev5:
            continue
        prev3_dirs = [1 if item.price_change_pct > 0 else (-1 if item.price_change_pct < 0 else 0) for item in prev3]
        pre_buy = sum(item.buy_vol for item in prev5)
        pre_sell = sum(item.sell_vol for item in prev5)
        pre_buy_ratio = pre_buy / (pre_buy + pre_sell) if pre_buy + pre_sell > 0 else float("nan")
        pre_cvd = sum(item.delta for item in prev5)
        pre_vol_avg = avg(item.volume_contracts for item in prev5)

        predictions: dict[str, str | None] = {
            "prev3_same_direction": None,
            "pre_buy_ratio_directional": None,
            "pre_cvd_directional": None,
            "quiet_pre_5m_any_direction": None,
        }
        if len(prev3_dirs) == 3 and all(direction == prev3_dirs[0] and direction != 0 for direction in prev3_dirs):
            predictions["prev3_same_direction"] = "long" if prev3_dirs[0] > 0 else "short"
        if math.isfinite(pre_buy_ratio):
            if pre_buy_ratio > 0.65:
                predictions["pre_buy_ratio_directional"] = "long"
            elif pre_buy_ratio < 0.35:
                predictions["pre_buy_ratio_directional"] = "short"
        if pre_cvd > 0:
            predictions["pre_cvd_directional"] = "long"
        elif pre_cvd < 0:
            predictions["pre_cvd_directional"] = "short"
        if pre_vol_avg < day_avg_vol.get(bar.date, float("nan")):
            predictions["quiet_pre_5m_any_direction"] = "any"

        for name, prediction in predictions.items():
            present = prediction is not None
            if present:
                rows[name]["present"] += 1
                if next_exp and (prediction == "any" or next_exp.direction == prediction):
                    rows[name]["present_correct"] += 1
            else:
                rows[name]["absent"] += 1
                if next_exp:
                    rows[name]["absent_correct"] += 1

    return [
        {
            "predictor": name,
            "present_n": row["present"],
            "present_wr": pct(row["present_correct"], row["present"]),
            "absent_n": row["absent"],
            "absent_wr": pct(row["absent_correct"], row["absent"]),
        }
        for name, row in sorted(rows.items())
    ]


def load_pump_trades(pair: str) -> list[dict[str, Any]]:
    signals = {row.get("signal_id"): row for row in read_jsonl(PUMP_SIGNALS) if row.get("type") == "ENTRY"}
    trades = []
    for label in read_jsonl(PUMP_LABELS):
        if label.get("type") != "EXIT" or label.get("sym") != pair:
            continue
        signal = signals.get(label.get("signal_id"))
        if not signal:
            continue
        trades.append({"signal": signal, "label": label})
    return trades


def trade_crosscheck(pair: str, bars: list[Bar], explosions: list[Explosion]) -> list[dict[str, Any]]:
    by_minute = {bar.minute_ms: bar for bar in bars}
    exp_minutes = {exp.minute_ms: exp for exp in explosions}
    out = []
    for trade in load_pump_trades(pair):
        signal = trade["signal"]
        label = trade["label"]
        opened = parse_dt(label.get("opened_at") or signal.get("ts_utc"))
        minute_ms = int(opened.timestamp() * 1000)
        minute_ms -= minute_ms % 60000
        direction = "long" if signal.get("direction") == "PUMP" else "short"
        entry = safe_float(label.get("entry_price") or signal.get("entry_open_price") or signal.get("signal_close"))
        tape_available = minute_ms in by_minute
        horizon_rows = [by_minute.get(minute_ms + h * 60000) for h in range(1, 6)]
        high = max((bar.high for bar in horizon_rows if bar), default=float("nan"))
        low = min((bar.low for bar in horizon_rows if bar), default=float("nan"))
        exit_by_horizon: dict[str, float | None] = {}
        for horizon in HORIZONS:
            close_bar = by_minute.get(minute_ms + horizon * 60000)
            if not close_bar or entry <= 0:
                exit_by_horizon[str(horizon)] = None
            elif direction == "long":
                exit_by_horizon[str(horizon)] = (close_bar.close - entry) / entry * 100
            else:
                exit_by_horizon[str(horizon)] = (entry - close_bar.close) / entry * 100
        if direction == "long":
            mfe_1_5 = (high - entry) / entry * 100 if math.isfinite(high) and entry > 0 else float("nan")
            mae_1_5 = (low - entry) / entry * 100 if math.isfinite(low) and entry > 0 else float("nan")
        else:
            mfe_1_5 = (entry - low) / entry * 100 if math.isfinite(low) and entry > 0 else float("nan")
            mae_1_5 = (entry - high) / entry * 100 if math.isfinite(high) and entry > 0 else float("nan")
        out.append(
            {
                "signal_id": signal.get("signal_id"),
                "opened_at": label.get("opened_at"),
                "direction": direction,
                "tape_available": tape_available,
                "on_explosion_bar": minute_ms in exp_minutes,
                "explosion_direction": exp_minutes.get(minute_ms).direction if minute_ms in exp_minutes else None,
                "label_exit_reason": label.get("exit_reason"),
                "label_net_pct": safe_float(label.get("net_pnl_pct")),
                "label_mfe_pct": safe_float(label.get("mfe_pct")),
                "label_mae_pct": safe_float(label.get("mae_pct")),
                "label_hold_min": safe_float(label.get("hold_min")),
                "tape_mfe_1_5m_pct": mfe_1_5,
                "tape_mae_1_5m_pct": mae_1_5,
                "exit_by_horizon_pct_before_fee": exit_by_horizon,
                "exit_3m_pct_before_fee": exit_by_horizon.get("3"),
            }
        )
    return out


def analyze_pair(pair: str, dates: list[str]) -> dict[str, Any]:
    bars_by_date = {date: aggregate_bars(pair, date) for date in dates}
    all_bars = [bar for date in dates for bar in bars_by_date[date]]
    explosions_by_date = {date: detect_explosions(bars_by_date[date]) for date in dates}
    all_explosions = [exp for date in dates for exp in explosions_by_date[date]]
    cross = trade_crosscheck(pair, all_bars, all_explosions)
    forward = summarize_forward(all_explosions)
    optimal_hold = max(
        ((h, payload) for h, payload in forward.items() if payload["n"] > 0),
        key=lambda item: item[1]["avg_forward_return_pct"],
        default=(None, None),
    )
    optimal_hold_key = str(optimal_hold[0]) if optimal_hold[0] is not None else None
    if optimal_hold_key:
        for row in cross:
            row["exit_optimal_hold_pct_before_fee"] = row["exit_by_horizon_pct_before_fee"].get(optimal_hold_key)
    else:
        for row in cross:
            row["exit_optimal_hold_pct_before_fee"] = None
    opposite_delays = [exp.next_opposite_min for exp in all_explosions if exp.next_opposite_min is not None]
    cycle_delays = [exp.next_same_after_opposite_min for exp in all_explosions if exp.next_same_after_opposite_min is not None]
    return {
        "pair": pair,
        "dates": dates,
        "bar_count": len(all_bars),
        "explosion_threshold_pct": EXPLOSION_THRESHOLD_PCT,
        "explosion_count": len(all_explosions),
        "daily_explosions": summarize_by_day(all_explosions),
        "avg_abs_explosion_size_pct": avg(abs(exp.size_pct) for exp in all_explosions),
        "forward": forward,
        "optimal_hold_min": int(optimal_hold[0]) if optimal_hold[0] is not None else None,
        "optimal_hold_avg_forward_return_pct": optimal_hold[1]["avg_forward_return_pct"] if optimal_hold[1] else None,
        "oscillation": {
            "opposite_n": len(opposite_delays),
            "median_next_opposite_min": median(opposite_delays) if opposite_delays else None,
            "avg_next_opposite_min": avg(float(v) for v in opposite_delays),
            "cycle_n": len(cycle_delays),
            "median_up_down_up_or_down_up_down_min": median(cycle_delays) if cycle_delays else None,
            "avg_cycle_min": avg(float(v) for v in cycle_delays),
        },
        "predictors": predictor_stats(all_bars, all_explosions),
        "pump_crosscheck": cross,
        "explosions": [asdict(exp) for exp in all_explosions],
    }


def fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(val):
        return "n/a"
    return f"{val:.2f}{suffix}"


def sample_label(n: int) -> str:
    return "preliminary" if n < 10 else "usable"


def render_report(results: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Volatility Scalper Tape Research",
        "",
        "Scope: AI and EDEN are analyzed separately. GAP rows are skipped; missing 1m bars are not interpolated.",
        f"Explosive candle threshold: `abs(price_change_pct) >= {EXPLOSION_THRESHOLD_PCT:.1f}%` on a 1m OHLCV bar.",
        "",
        "Forward return is measured from the explosive candle close to the horizon close in the entry direction.",
        "",
    ]
    for pair, result in results.items():
        lines.extend(
            [
                f"## {pair}",
                "",
                f"- bars: `{result['bar_count']}`",
                f"- explosive bars: `{result['explosion_count']}` ({sample_label(result['explosion_count'])})",
                f"- avg abs explosion size: `{fmt(result['avg_abs_explosion_size_pct'], '%')}`",
                f"- optimal hold by avg forward return: `{result['optimal_hold_min']}m`, avg `{fmt(result['optimal_hold_avg_forward_return_pct'], '%')}`",
                "",
                "### Explosive Candles Per Day",
                "",
                "| date | total | long | short | avg_abs_size |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in result["daily_explosions"]:
            lines.append(f"| {row['date']} | {row['total']} | {row['long']} | {row['short']} | {fmt(row['avg_abs_size_pct'], '%')} |")
        lines.extend(["", "### Forward After Explosive Candle", "", "| hold | n | avg_forward_return | continued | reversed_gt_0.5 |", "| ---: | ---: | ---: | ---: | ---: |"])
        for h in HORIZONS:
            row = result["forward"][str(h)]
            lines.append(
                f"| {h}m | {row['n']} | {fmt(row['avg_forward_return_pct'], '%')} | "
                f"{fmt(row['continued_pct'], '%')} | {fmt(row['reversed_gt_0p5_pct'], '%')} |"
            )
        osc = result["oscillation"]
        lines.extend(
            [
                "",
                "### Oscillation Rhythm",
                "",
                f"- next opposite explosive candle: n={osc['opposite_n']}, median={fmt(osc['median_next_opposite_min'], 'm')}, avg={fmt(osc['avg_next_opposite_min'], 'm')}",
                f"- full opposite-back cycle: n={osc['cycle_n']}, median={fmt(osc['median_up_down_up_or_down_up_down_min'], 'm')}, avg={fmt(osc['avg_cycle_min'], 'm')}",
                "",
                "### Pre-Explosion Predictors",
                "",
                "Predictor WR means: when the predictor fires on minute `t`, did minute `t+1` become an explosive candle in the predicted direction. For `quiet_pre_5m_any_direction`, either direction counts.",
                "",
                "| predictor | present_n | present_WR | absent_n | absent_next_explosion_rate |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in result["predictors"]:
            lines.append(f"| {row['predictor']} | {row['present_n']} | {fmt(row['present_wr'], '%')} | {row['absent_n']} | {fmt(row['absent_wr'], '%')} |")
        cross = result["pump_crosscheck"]
        covered_cross = [row for row in cross if row["tape_available"]]
        avg_label_net = avg(safe_float(row.get("label_net_pct")) for row in covered_cross)
        avg_exit3 = avg(safe_float(row.get("exit_3m_pct_before_fee")) for row in covered_cross)
        avg_exit_opt = avg(safe_float(row.get("exit_optimal_hold_pct_before_fee")) for row in covered_cross)
        lines.extend(
            [
                "",
                "### Real Pump Trade Cross-Check",
                "",
                f"- real pump trades in logs: `{len(cross)}`",
                f"- trades with entry-minute tape coverage: `{len(covered_cross)}` ({sample_label(len(covered_cross))})",
                f"- covered avg label net: `{fmt(avg_label_net, '%')}`",
                f"- covered avg 3m tape exit before fees: `{fmt(avg_exit3, '%')}`",
                f"- covered avg optimal-hold tape exit before fees: `{fmt(avg_exit_opt, '%')}`",
                "",
                "| signal_id | opened_at | dir | tape | explosion_bar | label | label_net | label_mfe | tape_mfe_1-5m | exit_3m | exit_opt | hold |",
                "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in cross:
            lines.append(
                f"| {row['signal_id']} | {row['opened_at']} | {row['direction']} | "
                f"{row['tape_available']} | {row['on_explosion_bar']} | {row['label_exit_reason']} | {fmt(row['label_net_pct'], '%')} | "
                f"{fmt(row['label_mfe_pct'], '%')} | {fmt(row['tape_mfe_1_5m_pct'], '%')} | "
                f"{fmt(row['exit_3m_pct_before_fee'], '%')} | {fmt(row.get('exit_optimal_hold_pct_before_fee'), '%')} | {fmt(row['label_hold_min'], 'm')} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Architecture Implications",
            "",
            "- If the best forward horizon is short and next-opposite median is also short, the new engine should treat explosive bars as scalp events, not hold events.",
            "- Add symmetric long/short handling: `PUMP` and `DUMP` waves are both first-class entries.",
            "- Add re-entry logic after an opposite explosive candle instead of session-level suppression after the first wave.",
            "- Use pre-event predictors only per-pair; AI and EDEN are not blended in this report.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {pair: analyze_pair(pair, dates) for pair, dates in PAIRS.items()}
    AI_PATH.write_text(json.dumps(results["AI-USDT-SWAP"], ensure_ascii=True, indent=2), encoding="utf-8")
    EDEN_PATH.write_text(json.dumps(results["EDEN-USDT-SWAP"], ensure_ascii=True, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(render_report(results), encoding="utf-8")
    print(f"saved {REPORT_PATH}")
    print(f"saved {AI_PATH}")
    print(f"saved {EDEN_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
