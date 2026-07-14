# -*- coding: utf-8 -*-
"""Private artifact writers for strategy-lab runs.

Everything written here goes to the private research root: metrics.json,
candidates.csv, graph edges, LLM review pack/prompt, summary, Obsidian notes,
and the candidate registry update.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from src.research_lab.candidate_registry import build_entry, registry_path, upsert_entries
from src.research_lab.experiment import ExperimentSpec, RunResult
from src.research_lab.paths import resolve_private_root
from src.research_lab.reducer import reduce_results
from src.research_lab.search_trial_evidence import (
    build_search_trial_evidence,
    write_search_trial_evidence,
)

VALIDATION_ORDER = ["FORWARD_PAPER", "REGIME_SPECIFIC", "OBSERVE", "REJECT"]
REGISTRY_STATUSES = {"FORWARD_PAPER", "REGIME_SPECIFIC", "OBSERVE"}
MAX_STORED_TRADES_PER_RESULT = 2000


def write_run_outputs(
    spec: ExperimentSpec,
    results: list[RunResult],
    out_root: Path,
    *,
    allow_public_output: bool = False,
    include_rejects: bool = False,
    runtime_meta: dict[str, Any] | None = None,
) -> Path:
    out_root = resolve_private_root(out_root, allow_public_output=allow_public_output)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_dir = out_root / "experiments" / "completed" / f"{stamp}_{spec.experiment_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    trial_evidence = build_search_trial_evidence(spec, results, runtime_meta)
    payload = {
        "schema": "strategy_lab_results.v1",
        "experiment_id": spec.experiment_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "timeframe": spec.timeframe,
        "requested_backend": spec.backend,
        "runtime": dict(runtime_meta or {}),
        "filters": spec.filters,
        "event_context": dict(spec.event_context or {}),
        "plan_meta": dict(spec.plan_meta or {}),
        "fees_bps": spec.fees_bps,
        "slippage_bps": spec.slippage_bps,
        "search_trial_evidence_id": trial_evidence["search_trial_evidence_id"],
        "multiple_testing_family_hash": trial_evidence["multiple_testing_family_hash"],
        "results": [result_dict(r, include_trades=True) for r in results],
    }
    (run_dir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_search_trial_evidence(run_dir, trial_evidence)
    _write_candidates_csv(run_dir / "candidates.csv", results)
    _write_graph_edges(run_dir / "graph_edges.csv", results)
    reduce_report = reduce_results(results)
    (run_dir / "reducer_report.json").write_text(
        json.dumps(reduce_report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "llm_review_pack.json").write_text(
        json.dumps(build_llm_review_pack(spec, results, reduce_report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "llm_review_prompt.md").write_text(
        _llm_review_prompt(spec, results, reduce_report), encoding="utf-8"
    )
    (run_dir / "summary.md").write_text(_summary_md(spec, results), encoding="utf-8")
    _write_obsidian_notes(spec, results, out_root, run_dir.name)
    artifact_label = f"experiments/completed/{run_dir.name}"
    # The registry tracks candidates worth revisiting. REJECT rows stay in the
    # full run artifacts (metrics.json / candidates.csv) but do not pollute the
    # registry unless explicitly requested for debugging.
    registrable = [
        r for r in results if include_rejects or r.validation_status in REGISTRY_STATUSES
    ]
    entries = [build_entry(spec.experiment_id, r, artifact_label, spec=spec) for r in registrable]
    if entries:
        upsert_entries(registry_path(out_root), entries)
    return run_dir


def result_dict(result: RunResult, *, include_trades: bool = False) -> dict[str, Any]:
    out = {
        "run_id": result.run_id,
        "symbol": result.symbol,
        "family": result.family,
        "params": result.params,
        "metrics": result.metrics,
        "decision": result.decision,
        "reasons": result.reasons,
        "validation_status": result.validation_status,
        "validation_reasons": result.validation_reasons,
        "risk_flags": result.risk_flags,
        "next_action": result.next_action,
        "regime_summary": result.regime_summary,
    }
    if include_trades:
        trades = list(result.trades or [])[:MAX_STORED_TRADES_PER_RESULT]
        out["trades"] = trades
        out["trades_stored"] = len(trades)
        out["trades_truncated"] = max(0, len(result.trades or []) - len(trades))
    return out


def _write_candidates_csv(path: Path, results: list[RunResult]) -> None:
    fields = [
        "run_id", "symbol", "family", "decision", "reasons", "n_trades",
        "win_rate", "avg_net_pct", "total_net_pct", "profit_factor",
        "max_drawdown_pct", "test_avg_net_pct", "best_trade_share",
        "validation_status", "validation_reasons", "next_action",
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
                "validation_status": r.validation_status,
                "validation_reasons": "|".join(r.validation_reasons),
                "next_action": r.next_action,
            })


def _write_graph_edges(path: Path, results: list[RunResult]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "relation", "weight", "run_id"])
        writer.writeheader()
        for r in results:
            writer.writerow({"source": r.symbol, "target": r.family, "relation": "tested_with", "weight": 1, "run_id": r.run_id})
            writer.writerow({"source": r.family, "target": r.decision, "relation": "produced", "weight": 1, "run_id": r.run_id})
            if r.validation_status:
                writer.writerow({"source": r.run_id, "target": r.validation_status, "relation": "validated_as", "weight": 1, "run_id": r.run_id})
            for reason in r.reasons:
                writer.writerow({"source": r.run_id, "target": reason, "relation": "has_reason", "weight": 1, "run_id": r.run_id})


def validation_counts(results: list[RunResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        key = r.validation_status or "UNKNOWN"
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_llm_review_pack(spec: ExperimentSpec, results: list[RunResult], reduce_report: Any = None) -> dict[str, Any]:
    by_decision: dict[str, int] = {}
    for r in results:
        by_decision[r.decision] = by_decision.get(r.decision, 0) + 1
    by_family: dict[str, dict[str, Any]] = {}
    for r in results:
        agg = by_family.setdefault(r.family, {"runs": 0, "sum_avg_net_pct": 0.0, "forward_paper": 0})
        agg["runs"] += 1
        agg["sum_avg_net_pct"] += float(r.metrics.get("avg_net_pct") or 0.0)
        if r.validation_status == "FORWARD_PAPER":
            agg["forward_paper"] += 1
    for agg in by_family.values():
        agg["mean_avg_net_pct"] = round(agg.pop("sum_avg_net_pct") / agg["runs"], 4) if agg["runs"] else 0.0
    top = sorted(
        results,
        key=lambda r: (
            -VALIDATION_ORDER.index(r.validation_status) if r.validation_status in VALIDATION_ORDER else -9,
            r.metrics.get("avg_net_pct") or 0.0,
        ),
        reverse=True,
    )[:10]
    pack = {
        "schema": "strategy_lab_llm_review_pack.v2",
        "instruction": (
            "Review aggregate research metrics only. You must NOT declare any "
            "candidate profitable or live-tradable. Look for overfit, weak OOS, "
            "regime dependence, late entries, and missing validation. Suggest next "
            "tests only — propose experiment specs as JSON drafts; a human must "
            "review and enqueue them manually."
        ),
        "experiment_id": spec.experiment_id,
        "filters": spec.filters,
        "counts": by_decision,
        "validation_counts": validation_counts(results),
        "family_aggregates": by_family,
        "entry_timing_aggregate": entry_timing_aggregate(results),
        "top_results": [result_dict(r) for r in top],
        "top_questions": [
            "Which FORWARD_PAPER groups survive a parameter-neighborhood re-test?",
            "Where is the entry late (low capture, high adverse excursion before it works)?",
            "Which results depend on a single regime bucket?",
            "What validation gate is still missing before any human review?",
        ],
        "required_followups": [
            "parameter neighborhood sweep around any FORWARD_PAPER candidate",
            "regime-filtered re-run for any REGIME_SPECIFIC candidate",
            "cost stress re-check before any promotion",
        ],
    }
    if reduce_report is not None:
        pack["reducer"] = reduce_report.to_dict()
    return pack


def entry_timing_aggregate(results: list[RunResult]) -> dict[str, Any]:
    """Mean of per-run entry-timing blocks (empty if none recorded)."""
    blocks = [r.metrics.get("entry_timing") for r in results if isinstance(r.metrics.get("entry_timing"), dict)]
    keys = ("avg_capture_ratio", "avg_mfe_pct", "avg_mae_pct", "late_entry_rate")
    out: dict[str, Any] = {}
    for k in keys:
        vals = [float(b[k]) for b in blocks if b.get(k) is not None]
        if vals:
            out[k] = round(sum(vals) / len(vals), 4)
    return out


def _llm_review_prompt(spec: ExperimentSpec, results: list[RunResult], reduce_report: Any = None) -> str:
    pack = build_llm_review_pack(spec, results, reduce_report)
    v_counts = validation_counts(results)
    return "\n".join(
        [
            "# Strategy Lab LLM Review Prompt",
            "",
            "You are reviewing a private trading research experiment.",
            "",
            "Rules:",
            "- Do not treat any result as live-tradable or profitable; statuses are research labels only.",
            "- Do not invent missing fills, fees, symbols, or validation results.",
            "- Look for overfit, small samples, single-trade dominance, weak OOS, regime dependence.",
            "- Recommend the next code-based pressure tests before any human considers the candidate.",
            "- You may propose next experiment specs (JSON drafts). They are NOT auto-enqueued;",
            "  a human must review them and run enqueue_experiment.py manually.",
            "- Keep exact private parameters and symbol findings inside the private research repo.",
            "",
            "Experiment:",
            f"- experiment_id: {spec.experiment_id}",
            f"- symbols: {', '.join(spec.symbols)}",
            f"- families: {', '.join(spec.families)}",
            f"- fees_bps: {spec.fees_bps}",
            f"- slippage_bps: {spec.slippage_bps}",
            f"- filters: {json.dumps(spec.filters) if spec.filters else 'none (all regimes)'}",
            "",
            "Validation counts:",
            *[f"- {k}: {v_counts.get(k, 0)}" for k in VALIDATION_ORDER],
            "",
            "Review tasks:",
            "1. Identify which FORWARD_PAPER / REGIME_SPECIFIC candidates are most likely overfit.",
            "2. List missing validation gates needed next.",
            "3. Suggest which family/filter/regime links should be tested next.",
            "4. Propose 1-3 small next experiment specs (JSON), without claiming profitability.",
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
    dirs = {
        name: vault / name
        for name in ("Runs", "Candidates", "Symbols", "Families", "Decisions", "Reasons", "Validation")
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    run_note = dirs["Runs"] / f"{_safe_note_name(run_name)}.md"
    run_links = []
    for r in sorted(results, key=lambda item: (item.decision, item.symbol, item.family, item.run_id)):
        note_id = _candidate_note_id(r)
        run_links.append(
            f"- [[Candidates/{note_id}|{r.run_id}]] - [[Symbols/{r.symbol}]] - "
            f"[[Families/{r.family}]] - [[Validation/{r.validation_status or 'UNKNOWN'}]]"
        )
    counts: dict[str, int] = {}
    for r in results:
        counts[r.decision] = counts.get(r.decision, 0) + 1
    v_counts = validation_counts(results)
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
                f"- filters: {json.dumps(spec.filters) if spec.filters else 'none'}",
                "",
                "## Decision Counts",
                "",
                *[f"- [[Decisions/{k}]]: {v}" for k, v in sorted(counts.items())],
                "",
                "## Validation Counts",
                "",
                *[f"- [[Validation/{k}]]: {v}" for k, v in sorted(v_counts.items())],
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
        _write_candidate_note(dirs["Candidates"] / f"{_candidate_note_id(r)}.md", r, run_name)
    for symbol in sorted({r.symbol for r in results}):
        _append_index_note(dirs["Symbols"] / f"{_safe_note_name(symbol)}.md", f"# {symbol}", _links_for(results, lambda r: r.symbol == symbol))
    for family in sorted({r.family for r in results}):
        _append_index_note(dirs["Families"] / f"{_safe_note_name(family)}.md", f"# {family}", _links_for(results, lambda r: r.family == family))
    for decision in sorted({r.decision for r in results}):
        _append_index_note(dirs["Decisions"] / f"{_safe_note_name(decision)}.md", f"# {decision}", _links_for(results, lambda r: r.decision == decision))
    for status in sorted({r.validation_status or "UNKNOWN" for r in results}):
        _append_index_note(dirs["Validation"] / f"{_safe_note_name(status)}.md", f"# {status}", _links_for(results, lambda r: (r.validation_status or "UNKNOWN") == status))
    for reason in sorted({reason for r in results for reason in r.reasons}):
        _append_index_note(dirs["Reasons"] / f"{_safe_note_name(reason)}.md", f"# {reason}", _links_for(results, lambda r: reason in r.reasons))


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
        f"- validation: [[Validation/{result.validation_status or 'UNKNOWN'}]]",
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
        f"- stress_avg_net_pct: {m.get('stress_avg_net_pct', 'n/a')}",
        "",
        "## Validation",
        "",
        f"- status: {result.validation_status or 'UNKNOWN'} (research label, not profitability)",
        f"- reasons: {', '.join(result.validation_reasons) or '-'}",
        f"- risk flags: {', '.join(result.risk_flags) or '-'}",
        f"- next action: {result.next_action or '-'}",
        f"- dominant regime: {result.regime_summary.get('dominant_bucket', 'n/a')}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _append_index_note(path: Path, title: str, links: list[str]) -> None:
    path.write_text("\n".join([title, "", "## Linked Candidates", "", *sorted(set(links)), ""]), encoding="utf-8")


def _links_for(results: list[RunResult], match) -> list[str]:
    return [
        f"- [[Candidates/{_candidate_note_id(r)}|{r.run_id}]] - [[Symbols/{r.symbol}]] - "
        f"[[Families/{r.family}]] - [[Validation/{r.validation_status or 'UNKNOWN'}]]"
        for r in results
        if match(r)
    ]


def _summary_md(spec: ExperimentSpec, results: list[RunResult]) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.decision] = counts.get(r.decision, 0) + 1
    v_counts = validation_counts(results)
    lines = [
        f"# Strategy Lab Run: {spec.experiment_id}",
        "",
        "Private research output. Do not publish exact result tables.",
        "",
        "## Decision Counts",
        "",
        *[f"- {key}: {counts[key]}" for key in sorted(counts)],
        "",
        "## Validation Counts",
        "",
        *[f"- {key}: {v_counts[key]}" for key in sorted(v_counts)],
        "",
        "## Top Rows",
        "",
    ]
    top = sorted(results, key=lambda r: r.metrics["avg_net_pct"], reverse=True)[:12]
    lines.append("| run_id | symbol | family | decision | validation | trades | avg_net_pct | test_avg_net_pct | pf | reasons |")
    lines.append("|---|---|---|---|---|---:|---:|---:|---:|---|")
    for r in top:
        m = r.metrics
        lines.append(
            f"| {r.run_id} | {r.symbol} | {r.family} | {r.decision} | {r.validation_status} | "
            f"{m['n_trades']} | {m['avg_net_pct']} | {m['test_avg_net_pct']} | "
            f"{m['profit_factor']} | {'; '.join(r.reasons)} |"
        )
    lines.append("")
    return "\n".join(lines)
