# -*- coding: utf-8 -*-
"""Small deterministic strategy-discovery runner.

The public code defines how experiments are evaluated. The private research
repository stores the actual result tables, candidate scorecards, and graph
exports.
"""

from __future__ import annotations

import csv
import datetime as dt
import glob
import hashlib
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    data_glob: str
    symbols: list[str]
    families: list[str]
    parameter_grid: dict[str, list[dict[str, Any]]]
    fees_bps: float = 7.0
    slippage_bps: float = 3.0
    min_trades: int = 20
    split_ratio: float = 0.7
    max_runs: int = 0

    @classmethod
    def from_json(cls, path: Path) -> "ExperimentSpec":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            experiment_id=str(data["experiment_id"]),
            data_glob=str(data["data_glob"]),
            symbols=[str(s) for s in data.get("symbols", [])],
            families=[str(f) for f in data.get("families", [])],
            parameter_grid={str(k): list(v) for k, v in data.get("parameter_grid", {}).items()},
            fees_bps=float(data.get("fees_bps", 7.0)),
            slippage_bps=float(data.get("slippage_bps", 3.0)),
            min_trades=int(data.get("min_trades", 20)),
            split_ratio=float(data.get("split_ratio", 0.7)),
            max_runs=int(data.get("max_runs", 0)),
        )


@dataclass(frozen=True)
class RunResult:
    run_id: str
    symbol: str
    family: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    decision: str
    reasons: list[str]


def load_candles(path: Path) -> list[dict[str, float | int | str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, float | int | str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                {
                    "ts": int(row["ts"]),
                    "date": str(row.get("date") or ""),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "vol": float(row.get("vol") or 0.0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda r: int(r["ts"]))


def choose_symbol_file(pattern: str, symbol: str) -> Path | None:
    normalized = symbol.replace("-", "_").replace("/", "_")
    candidates = [Path(p) for p in glob.glob(pattern.format(symbol=normalized))]
    if not candidates:
        candidates = [Path(p) for p in glob.glob(pattern.format(symbol=symbol))]
    if not candidates:
        return None
    # Prefer the file with the largest number of candles, then deterministic path.
    return sorted(candidates, key=lambda p: (_safe_row_count(p), str(p)), reverse=True)[0]


def _safe_row_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def moving_average(values: list[float], idx: int, lookback: int) -> float | None:
    if idx - lookback < 0:
        return None
    window = values[idx - lookback:idx]
    return sum(window) / len(window) if window else None


def generate_signals(
    candles: list[dict[str, Any]],
    family: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    if family == "momentum_breakout":
        return _signals_momentum_breakout(candles, params)
    if family == "mean_reversion_fade":
        return _signals_mean_reversion_fade(candles, params)
    if family == "volume_shock_continuation":
        return _signals_volume_shock(candles, params)
    raise ValueError(f"unknown family: {family}")


def _signals_momentum_breakout(candles: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("lookback", 20))
    threshold_pct = float(params.get("threshold_pct", 0.0))
    signals = []
    for idx in range(lookback, len(candles) - 1):
        prev = candles[idx - lookback:idx]
        high = max(float(r["high"]) for r in prev)
        low = min(float(r["low"]) for r in prev)
        close = float(candles[idx]["close"])
        if close > high * (1 + threshold_pct / 100):
            signals.append({"idx": idx + 1, "side": "long", "reason": "breakout_high"})
        elif close < low * (1 - threshold_pct / 100):
            signals.append({"idx": idx + 1, "side": "short", "reason": "breakout_low"})
    return signals


def _signals_mean_reversion_fade(candles: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("lookback", 5))
    threshold_pct = float(params.get("move_pct", 8.0))
    signals = []
    for idx in range(lookback, len(candles) - 1):
        base = float(candles[idx - lookback]["close"])
        close = float(candles[idx]["close"])
        if base <= 0:
            continue
        move = (close / base - 1) * 100
        if move >= threshold_pct:
            signals.append({"idx": idx + 1, "side": "short", "reason": "fade_up_move"})
        elif move <= -threshold_pct:
            signals.append({"idx": idx + 1, "side": "long", "reason": "fade_down_move"})
    return signals


def _signals_volume_shock(candles: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    lookback = int(params.get("lookback", 20))
    vol_mult = float(params.get("vol_mult", 2.0))
    min_body_pct = float(params.get("min_body_pct", 3.0))
    vols = [float(c["vol"]) for c in candles]
    signals = []
    for idx in range(lookback, len(candles) - 1):
        avg_vol = moving_average(vols, idx, lookback)
        if not avg_vol:
            continue
        cur = candles[idx]
        open_ = float(cur["open"])
        close = float(cur["close"])
        if open_ <= 0:
            continue
        body = (close / open_ - 1) * 100
        if float(cur["vol"]) >= avg_vol * vol_mult and abs(body) >= min_body_pct:
            signals.append({
                "idx": idx + 1,
                "side": "long" if body > 0 else "short",
                "reason": "volume_shock",
            })
    return signals


def simulate_trades(
    candles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    params: dict[str, Any],
    *,
    fees_bps: float,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    hold_bars = int(params.get("hold_bars", 5))
    stop_pct = float(params.get("stop_pct", 0.0))
    take_pct = float(params.get("take_pct", 0.0))
    cost_pct = (fees_bps + slippage_bps) / 10000.0
    trades = []
    for sig in signals:
        idx = int(sig["idx"])
        exit_idx = min(idx + hold_bars, len(candles) - 1)
        if idx >= len(candles) or exit_idx <= idx:
            continue
        side = str(sig["side"])
        entry = float(candles[idx]["open"])
        if entry <= 0:
            continue
        exit_price = float(candles[exit_idx]["close"])
        outcome = "time_exit"
        for j in range(idx, exit_idx + 1):
            high = float(candles[j]["high"])
            low = float(candles[j]["low"])
            if side == "long":
                stop = entry * (1 - stop_pct / 100) if stop_pct > 0 else None
                take = entry * (1 + take_pct / 100) if take_pct > 0 else None
                if stop and low <= stop:
                    exit_price, outcome = stop, "stop"
                    break
                if take and high >= take:
                    exit_price, outcome = take, "take"
                    break
            else:
                stop = entry * (1 + stop_pct / 100) if stop_pct > 0 else None
                take = entry * (1 - take_pct / 100) if take_pct > 0 else None
                if stop and high >= stop:
                    exit_price, outcome = stop, "stop"
                    break
                if take and low <= take:
                    exit_price, outcome = take, "take"
                    break
        direction = 1 if side == "long" else -1
        ret_pct = direction * (exit_price / entry - 1) * 100
        net_pct = ret_pct - cost_pct * 100
        trades.append(
            {
                "entry_ts": candles[idx]["ts"],
                "exit_ts": candles[exit_idx]["ts"],
                "side": side,
                "entry": round(entry, 10),
                "exit": round(exit_price, 10),
                "net_pct": round(net_pct, 4),
                "outcome": outcome,
                "reason": sig.get("reason"),
            }
        )
    return trades


def compute_metrics(trades: list[dict[str, Any]], split_ratio: float, min_trades: int) -> dict[str, Any]:
    n = len(trades)
    returns = [float(t["net_pct"]) for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    split = max(1, min(n, int(n * split_ratio))) if n else 0
    train = returns[:split]
    test = returns[split:]
    total = sum(returns)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    best_share = max((abs(r) for r in returns), default=0.0) / abs(total) if total else 0.0
    return {
        "n_trades": n,
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "avg_net_pct": round(total / n, 4) if n else 0.0,
        "total_net_pct": round(total, 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else (99.0 if wins else 0.0),
        "max_drawdown_pct": round(_max_drawdown(returns), 4),
        "best_trade_share": round(best_share, 4),
        "train_avg_net_pct": round(sum(train) / len(train), 4) if train else 0.0,
        "test_avg_net_pct": round(sum(test) / len(test), 4) if test else 0.0,
        "test_trades": len(test),
        "min_trades": min_trades,
    }


def _max_drawdown(returns: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for ret in returns:
        equity += ret
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


def grade_candidate(metrics: dict[str, Any]) -> tuple[str, list[str]]:
    reasons = []
    if metrics["n_trades"] < metrics["min_trades"]:
        reasons.append("too_few_trades")
    if metrics["profit_factor"] < 1.15:
        reasons.append("weak_profit_factor")
    if metrics["avg_net_pct"] <= 0:
        reasons.append("negative_or_flat_average")
    if metrics["test_trades"] < 5:
        reasons.append("weak_oos_sample")
    if metrics["test_avg_net_pct"] <= 0:
        reasons.append("oos_not_positive")
    if metrics["best_trade_share"] > 0.45:
        reasons.append("single_trade_dominance")
    if metrics["max_drawdown_pct"] > abs(metrics["total_net_pct"]) * 0.8 and metrics["total_net_pct"] > 0:
        reasons.append("drawdown_too_large_vs_total")

    if not reasons:
        return "PROMOTE_FOR_PRESSURE_TEST", ["passed_basic_gates"]
    if metrics["n_trades"] >= metrics["min_trades"] and metrics["avg_net_pct"] > 0 and metrics["test_avg_net_pct"] > 0:
        return "OBSERVE", reasons
    return "REJECT", reasons


def stable_run_id(symbol: str, family: str, params: dict[str, Any]) -> str:
    raw = json.dumps({"symbol": symbol, "family": family, "params": params}, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def evaluate_spec(spec: ExperimentSpec) -> list[RunResult]:
    results = []
    for symbol, family in itertools.product(spec.symbols, spec.families):
        path = choose_symbol_file(spec.data_glob, symbol)
        if not path:
            continue
        candles = load_candles(path)
        for params in spec.parameter_grid.get(family, []):
            signals = generate_signals(candles, family, params)
            trades = simulate_trades(
                candles,
                signals,
                params,
                fees_bps=spec.fees_bps,
                slippage_bps=spec.slippage_bps,
            )
            metrics = compute_metrics(trades, spec.split_ratio, spec.min_trades)
            metrics["data_file"] = str(path)
            decision, reasons = grade_candidate(metrics)
            results.append(
                RunResult(
                    run_id=stable_run_id(symbol, family, params),
                    symbol=symbol,
                    family=family,
                    params=params,
                    metrics=metrics,
                    decision=decision,
                    reasons=reasons,
                )
            )
            if spec.max_runs and len(results) >= spec.max_runs:
                return results
    return results


def write_run_outputs(spec: ExperimentSpec, results: list[RunResult], out_root: Path) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = out_root / "experiments" / "completed" / f"{stamp}_{spec.experiment_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "strategy_lab_results.v0",
        "experiment_id": spec.experiment_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "results": [_result_dict(r) for r in results],
    }
    (run_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_candidates_csv(run_dir / "candidates.csv", results)
    _write_graph_edges(run_dir / "graph_edges.csv", results)
    (run_dir / "llm_review_pack.json").write_text(
        json.dumps(_llm_review_pack(spec, results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "llm_review_prompt.md").write_text(_llm_review_prompt(spec, results), encoding="utf-8")
    (run_dir / "summary.md").write_text(_summary_md(spec, results), encoding="utf-8")
    _write_obsidian_notes(spec, results, out_root, run_dir.name)
    return run_dir


def _result_dict(result: RunResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "symbol": result.symbol,
        "family": result.family,
        "params": result.params,
        "metrics": result.metrics,
        "decision": result.decision,
        "reasons": result.reasons,
    }


def _write_candidates_csv(path: Path, results: list[RunResult]) -> None:
    fields = [
        "run_id", "symbol", "family", "decision", "reasons", "n_trades",
        "win_rate", "avg_net_pct", "total_net_pct", "profit_factor",
        "max_drawdown_pct", "test_avg_net_pct", "best_trade_share",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in results:
            m = r.metrics
            writer.writerow({
                "run_id": r.run_id,
                "symbol": r.symbol,
                "family": r.family,
                "decision": r.decision,
                "reasons": "|".join(r.reasons),
                "n_trades": m["n_trades"],
                "win_rate": m["win_rate"],
                "avg_net_pct": m["avg_net_pct"],
                "total_net_pct": m["total_net_pct"],
                "profit_factor": m["profit_factor"],
                "max_drawdown_pct": m["max_drawdown_pct"],
                "test_avg_net_pct": m["test_avg_net_pct"],
                "best_trade_share": m["best_trade_share"],
            })


def _write_graph_edges(path: Path, results: list[RunResult]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "relation", "weight", "run_id"])
        writer.writeheader()
        for r in results:
            writer.writerow({"source": r.symbol, "target": r.family, "relation": "tested_with", "weight": 1, "run_id": r.run_id})
            writer.writerow({"source": r.family, "target": r.decision, "relation": "produced", "weight": 1, "run_id": r.run_id})
            for reason in r.reasons:
                writer.writerow({"source": r.run_id, "target": reason, "relation": "has_reason", "weight": 1, "run_id": r.run_id})


def _llm_review_pack(spec: ExperimentSpec, results: list[RunResult]) -> dict[str, Any]:
    by_decision: dict[str, int] = {}
    for r in results:
        by_decision[r.decision] = by_decision.get(r.decision, 0) + 1
    top = sorted(results, key=lambda r: (r.decision == "PROMOTE_FOR_PRESSURE_TEST", r.metrics["avg_net_pct"]), reverse=True)[:10]
    return {
        "schema": "strategy_lab_llm_review_pack.v0",
        "instruction": "Review aggregate metrics only. Do not infer live tradability. Look for overfit and missing validation.",
        "experiment_id": spec.experiment_id,
        "counts": by_decision,
        "top_results": [_result_dict(r) for r in top],
    }


def _llm_review_prompt(spec: ExperimentSpec, results: list[RunResult]) -> str:
    pack = _llm_review_pack(spec, results)
    promoted = [r for r in results if r.decision == "PROMOTE_FOR_PRESSURE_TEST"]
    observed = [r for r in results if r.decision == "OBSERVE"]
    rejected = [r for r in results if r.decision == "REJECT"]
    return "\n".join(
        [
            "# Strategy Lab LLM Review Prompt",
            "",
            "You are reviewing a private trading research experiment.",
            "",
            "Rules:",
            "- Do not treat any result as live-tradable.",
            "- Do not invent missing fills, fees, symbols, or validation results.",
            "- Look for overfit, small samples, single-trade dominance, weak OOS, and missing regime controls.",
            "- Recommend the next code-based pressure tests before any human considers the candidate.",
            "- Keep exact private parameters and symbol findings inside the private research repo.",
            "",
            "Experiment:",
            f"- experiment_id: {spec.experiment_id}",
            f"- symbols: {', '.join(spec.symbols)}",
            f"- families: {', '.join(spec.families)}",
            f"- fees_bps: {spec.fees_bps}",
            f"- slippage_bps: {spec.slippage_bps}",
            "",
            "Counts:",
            f"- promote_for_pressure_test: {len(promoted)}",
            f"- observe: {len(observed)}",
            f"- reject: {len(rejected)}",
            "",
            "Review tasks:",
            "1. Identify which promoted candidates are most likely overfit.",
            "2. List missing validation gates needed next.",
            "3. Suggest which family/filter/regime links should be tested next.",
            "4. Propose a small next experiment spec, without claiming profitability.",
            "5. Produce a short operator summary for the Obsidian run note.",
            "",
            "Machine-readable pack follows:",
            "",
            "```json",
            json.dumps(pack, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )


def _safe_note_name(value: str) -> str:
    keep = []
    for ch in value:
        if ch.isalnum() or ch in {"_", "-", "."}:
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep).strip("_") or "unknown"


def _write_obsidian_notes(spec: ExperimentSpec, results: list[RunResult], out_root: Path, run_name: str) -> None:
    vault = out_root / "obsidian-vault"
    runs_dir = vault / "Runs"
    candidates_dir = vault / "Candidates"
    symbols_dir = vault / "Symbols"
    families_dir = vault / "Families"
    decisions_dir = vault / "Decisions"
    reasons_dir = vault / "Reasons"
    for path in (runs_dir, candidates_dir, symbols_dir, families_dir, decisions_dir, reasons_dir):
        path.mkdir(parents=True, exist_ok=True)

    run_note = runs_dir / f"{_safe_note_name(run_name)}.md"
    run_links = []
    for r in sorted(results, key=lambda item: (item.decision, item.symbol, item.family, item.run_id)):
        note_id = _candidate_note_id(r)
        run_links.append(f"- [[Candidates/{note_id}|{r.run_id}]] - [[Symbols/{r.symbol}]] - [[Families/{r.family}]] - [[Decisions/{r.decision}]]")
    counts: dict[str, int] = {}
    for r in results:
        counts[r.decision] = counts.get(r.decision, 0) + 1
    run_note.write_text(
        "\n".join(
            [
                f"# {run_name}",
                "",
                "Private strategy-lab run. Exact result tables stay private.",
                "",
                "## Experiment",
                "",
                f"- experiment: {spec.experiment_id}",
                f"- families: {', '.join(f'[[Families/{f}]]' for f in spec.families)}",
                f"- symbols: {', '.join(f'[[Symbols/{s}]]' for s in spec.symbols)}",
                "",
                "## Counts",
                "",
                *[f"- [[Decisions/{k}]]: {v}" for k, v in sorted(counts.items())],
                "",
                "## Candidate Links",
                "",
                *run_links,
                "",
            ]
        ),
        encoding="utf-8",
    )

    for r in results:
        _write_candidate_note(candidates_dir / f"{_candidate_note_id(r)}.md", r, run_name)
    for symbol in sorted({r.symbol for r in results}):
        _append_index_note(symbols_dir / f"{_safe_note_name(symbol)}.md", f"# {symbol}", _links_for_symbol(results, symbol))
    for family in sorted({r.family for r in results}):
        _append_index_note(families_dir / f"{_safe_note_name(family)}.md", f"# {family}", _links_for_family(results, family))
    for decision in sorted({r.decision for r in results}):
        _append_index_note(decisions_dir / f"{_safe_note_name(decision)}.md", f"# {decision}", _links_for_decision(results, decision))
    for reason in sorted({reason for r in results for reason in r.reasons}):
        _append_index_note(reasons_dir / f"{_safe_note_name(reason)}.md", f"# {reason}", _links_for_reason(results, reason))


def _candidate_note_id(result: RunResult) -> str:
    return _safe_note_name(f"{result.run_id}_{result.symbol}_{result.family}")


def _write_candidate_note(path: Path, result: RunResult, run_name: str) -> None:
    m = result.metrics
    lines = [
        f"# Candidate {result.run_id}",
        "",
        f"- run: [[Runs/{_safe_note_name(run_name)}]]",
        f"- symbol: [[Symbols/{result.symbol}]]",
        f"- family: [[Families/{result.family}]]",
        f"- decision: [[Decisions/{result.decision}]]",
        f"- reasons: {', '.join(f'[[Reasons/{reason}]]' for reason in result.reasons)}",
        "",
        "## Metrics",
        "",
        f"- trades: {m['n_trades']}",
        f"- win_rate: {m['win_rate']}",
        f"- avg_net_pct: {m['avg_net_pct']}",
        f"- test_avg_net_pct: {m['test_avg_net_pct']}",
        f"- profit_factor: {m['profit_factor']}",
        f"- max_drawdown_pct: {m['max_drawdown_pct']}",
        f"- best_trade_share: {m['best_trade_share']}",
        "",
        "## Next Review",
        "",
        "- Needs code pressure-test before any live interpretation.",
        "- Check OOS split, costs, parameter neighborhood, and regime dependency.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_index_note(path: Path, title: str, links: list[str]) -> None:
    path.write_text("\n".join([title, "", "## Linked Candidates", "", *sorted(set(links)), ""]), encoding="utf-8")


def _links_for_symbol(results: list[RunResult], symbol: str) -> list[str]:
    return [f"- [[Candidates/{_candidate_note_id(r)}|{r.run_id}]] - [[Families/{r.family}]] - [[Decisions/{r.decision}]]" for r in results if r.symbol == symbol]


def _links_for_family(results: list[RunResult], family: str) -> list[str]:
    return [f"- [[Candidates/{_candidate_note_id(r)}|{r.run_id}]] - [[Symbols/{r.symbol}]] - [[Decisions/{r.decision}]]" for r in results if r.family == family]


def _links_for_decision(results: list[RunResult], decision: str) -> list[str]:
    return [f"- [[Candidates/{_candidate_note_id(r)}|{r.run_id}]] - [[Symbols/{r.symbol}]] - [[Families/{r.family}]]" for r in results if r.decision == decision]


def _links_for_reason(results: list[RunResult], reason: str) -> list[str]:
    return [f"- [[Candidates/{_candidate_note_id(r)}|{r.run_id}]] - [[Symbols/{r.symbol}]] - [[Families/{r.family}]]" for r in results if reason in r.reasons]


def _summary_md(spec: ExperimentSpec, results: list[RunResult]) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.decision] = counts.get(r.decision, 0) + 1
    lines = [
        f"# Strategy Lab Run: {spec.experiment_id}",
        "",
        "Private research output. Do not publish exact result tables.",
        "",
        "## Counts",
        "",
    ]
    for key in sorted(counts):
        lines.append(f"- {key}: {counts[key]}")
    lines.extend(["", "## Top Rows", ""])
    top = sorted(results, key=lambda r: r.metrics["avg_net_pct"], reverse=True)[:12]
    lines.append("| run_id | symbol | family | decision | trades | avg_net_pct | test_avg_net_pct | pf | reasons |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---|")
    for r in top:
        m = r.metrics
        lines.append(
            f"| {r.run_id} | {r.symbol} | {r.family} | {r.decision} | "
            f"{m['n_trades']} | {m['avg_net_pct']} | {m['test_avg_net_pct']} | "
            f"{m['profit_factor']} | {'; '.join(r.reasons)} |"
        )
    lines.append("")
    return "\n".join(lines)
