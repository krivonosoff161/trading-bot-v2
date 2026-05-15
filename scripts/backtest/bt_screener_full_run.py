from __future__ import annotations

import contextlib
import io
import json
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any

import bt_screener_report
import bt_screener_sim as sim


ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "scripts" / "backtest" / "results"
FULL_LOG = RESULTS_DIR / "full_run.log"
REPORT_FULL = RESULTS_DIR / "report_full.txt"
REPORT_FINAL = RESULTS_DIR / "report_final.txt"


def log(message: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with FULL_LOG.open("a", encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")


def log_error(label: str, exc: BaseException) -> None:
    log(f"[ERROR] {label}: {type(exc).__name__}: {exc}")
    log(traceback.format_exc())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [float(row.get("pnl_pct", 0.0)) for row in rows if float(row.get("pnl_pct", 0.0)) > 0]
    losses = [float(row.get("pnl_pct", 0.0)) for row in rows if float(row.get("pnl_pct", 0.0)) < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "signals": len(rows),
        "wr": len(wins) / len(rows) * 100.0 if rows else 0.0,
        "pf": gross_win / gross_loss if gross_loss else (99.0 if gross_win else 0.0),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
    }


def fmt_pf(value: float) -> str:
    return "inf" if value >= 99 else f"{value:.2f}"


def run_status(run_id: int, rows: list[dict[str, Any]]) -> str:
    st = stats(rows)
    return f"Run {run_id} done — signals={st['signals']} WR={st['wr']:.1f}% PF={fmt_pf(st['pf'])}"


def run_one(
    run: dict[str, Any],
    data: dict[str, dict[str, list[list[float | int]]]],
    strategy_config: dict[str, Any],
    cooldown_minutes: int,
) -> list[dict[str, Any]]:
    sweep_params = None
    fallback_profile = None
    if run["params"] == "sweep":
        sweep_params = sim.optimize_sweep(run, data, strategy_config, cooldown_minutes)
        log(f"Run {run['id']} sweep selected: {sweep_params}")
    if run["params"] in {"dynamic", "dynamic_cvd"}:
        fallback_profile = sim.global_fallback_profile(data, strategy_config)
        log(f"Run {run['id']} dynamic fallback profile: {fallback_profile}")
    return sim.simulate_run(
        run,
        data,
        strategy_config,
        write=True,
        sweep_params=sweep_params,
        dynamic_fallback_profile_data=fallback_profile,
        cooldown_minutes=cooldown_minutes,
        quiet=True,
    )


def save_report_full() -> None:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bt_screener_report.main()
    REPORT_FULL.write_text(buf.getvalue(), encoding="utf-8")


def run_dynamic_variant(
    label: str,
    adx_pct: float,
    vol_pct: float,
    data: dict[str, dict[str, list[list[float | int]]]],
    strategy_config: dict[str, Any],
    cooldown_minutes: int,
) -> None:
    old_adx = sim.DYNAMIC_ADX_PERCENTILE
    old_vol = sim.DYNAMIC_VOL_PERCENTILE
    try:
        sim.DYNAMIC_ADX_PERCENTILE = adx_pct
        sim.DYNAMIC_VOL_PERCENTILE = vol_pct
        fallback = sim.global_fallback_profile(data, strategy_config)
        out = RESULTS_DIR / f"screener_run_5{label}.jsonl"
        rows = sim.simulate_run(
            {"id": f"5{label}", "params": "dynamic", "tf_signal": "15m"},
            data,
            strategy_config,
            write=True,
            dynamic_fallback_profile_data=fallback,
            cooldown_minutes=cooldown_minutes,
            quiet=True,
            output_path=out,
        )
        log(f"Variant 5{label}: adx_p{adx_pct:g} vol_p{vol_pct:g} {stats(rows)}")
    finally:
        sim.DYNAMIC_ADX_PERCENTILE = old_adx
        sim.DYNAMIC_VOL_PERCENTILE = old_vol


def run_hardcode_sweep_variant(
    adx: float,
    vol: float,
    data: dict[str, dict[str, list[list[float | int]]]],
    strategy_config: dict[str, Any],
    cooldown_minutes: int,
) -> None:
    vol_tag = str(vol).replace(".", "p")
    out = RESULTS_DIR / f"screener_sweep_hardcode_adx{int(adx)}_vol{vol_tag}.jsonl"
    rows = sim.simulate_run(
        {"id": f"hc_{int(adx)}_{vol_tag}", "params": "sweep", "tf_signal": "15m"},
        data,
        strategy_config,
        write=True,
        sweep_params={"adx": adx, "vol_ratio": vol, "bb_width": None},
        cooldown_minutes=cooldown_minutes,
        quiet=True,
        output_path=out,
    )
    log(f"Hardcode sweep adx={adx:g} vol={vol:g}: {stats(rows)}")


def write_final_report() -> None:
    paths = sorted(RESULTS_DIR.glob("screener_run_*.jsonl")) + sorted(RESULTS_DIR.glob("screener_sweep_hardcode_*.jsonl"))
    lines: list[str] = []
    lines.append("FILE                                           SIGNALS  WR%    PF    AVG_WIN  AVG_LOSS")
    payloads: list[tuple[Path, list[dict[str, Any]], dict[str, Any]]] = []
    for path in paths:
        rows = load_jsonl(path)
        if not rows:
            continue
        st = stats(rows)
        payloads.append((path, rows, st))
        lines.append(
            f"{path.name:<46} {st['signals']:>7}  {st['wr']:>5.1f}%  {fmt_pf(st['pf']):>5}  "
            f"{st['avg_win']:>+7.2f}%  {st['avg_loss']:>+8.2f}%"
        )
    lines.append("")
    for path, rows, _st in payloads:
        lines.append(path.name)
        by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_regime[str(row.get("regime", "UNKNOWN"))].append(row)
            by_pair[str(row.get("symbol", "UNKNOWN"))].append(row)
        lines.append("  Regimes:")
        for regime in ("TRENDING", "DRIFT", "RANGING", "CHOPPY"):
            st = stats(by_regime.get(regime, []))
            if st["signals"]:
                lines.append(f"    {regime:<9} n={st['signals']:<4} WR={st['wr']:.1f}% PF={fmt_pf(st['pf'])}")
        pair_stats = [(symbol, stats(items)) for symbol, items in by_pair.items()]
        pair_stats.sort(key=lambda item: (item[1]["pf"], item[1]["signals"]), reverse=True)
        lines.append("  Top 5 pairs:")
        for symbol, st in pair_stats[:5]:
            lines.append(f"    {symbol:<14} n={st['signals']:<4} WR={st['wr']:.1f}% PF={fmt_pf(st['pf'])}")
        lines.append("")
    REPORT_FINAL.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    data = sim.load_cache()
    strategy_config = sim.load_strategy_config()
    cooldown_minutes = sim.load_cooldown_minutes()
    runs_by_id = {int(run["id"]): run for run in sim.RUNS}

    for run_id in range(3, 9):
        try:
            rows = run_one(runs_by_id[run_id], data, strategy_config, cooldown_minutes)
            status = run_status(run_id, rows)
            print(status, flush=True)
            log(status)
        except Exception as exc:
            log_error(f"run {run_id}", exc)
            status = f"Run {run_id} done — signals=0 WR=0.0% PF=0.00"
            print(status, flush=True)
            log(status)

    save_report_full()

    run1 = stats(load_jsonl(RESULTS_DIR / "screener_run_1_hardcode_15m.jsonl"))
    run2 = stats(load_jsonl(RESULTS_DIR / "screener_run_2_hardcode_5m.jsonl"))
    run5 = stats(load_jsonl(RESULTS_DIR / "screener_run_5_dynamic_15m.jsonl"))
    run6 = stats(load_jsonl(RESULTS_DIR / "screener_run_6_dynamic_5m.jsonl"))
    hard_wr = (run1["wr"] + run2["wr"]) / 2
    dynamic_wr = (run5["wr"] + run6["wr"]) / 2

    if dynamic_wr >= hard_wr + 3.0:
        for label, adx_pct, vol_pct in (("a", 60, 70), ("b", 70, 75), ("c", 80, 80)):
            try:
                run_dynamic_variant(label, adx_pct, vol_pct, data, strategy_config, cooldown_minutes)
            except Exception as exc:
                log_error(f"dynamic variant 5{label}", exc)
    else:
        for adx in (15, 18, 22):
            for vol in (0.7, 1.0, 1.3):
                try:
                    run_hardcode_sweep_variant(adx, vol, data, strategy_config, cooldown_minutes)
                except Exception as exc:
                    log_error(f"hardcode sweep adx={adx} vol={vol}", exc)

    write_final_report()


if __name__ == "__main__":
    main()
