import ast
import json
from pathlib import Path

from src.research_lab.farm_tasks_db import FarmTasksDB, tasks_db_path
from src.research_lab.outcome_retest_result import build_outcome_retest_results


def _completed_retest(root: Path, *, n_trades: int = 20, avg_net_pct: float = 0.4) -> None:
    run_label = "experiments/completed/retest_run"
    run_dir = root / run_label
    run_dir.mkdir(parents=True)
    context = {
        "origin": "outcome_retest",
        "retest_id": "ort_1",
        "review_id": "review_1",
        "source_ref": "training_1",
        "paper_signal_id": "sig_1",
        "baseline": {"net_pct": -0.8},
        "selection_window_start": "2026-01-01T00:00:00+00:00",
        "selection_window_end": "2026-02-01T00:00:00+00:00",
    }
    (run_dir / "metrics.json").write_text(
        json.dumps(
            {
                "timeframe": "1h",
                "event_context": context,
                "results": [
                    {
                        "run_id": "run_1",
                        "symbol": "BTC_USDT_SWAP",
                        "family": "momentum_breakout",
                        "validation_status": "OBSERVE",
                        "metrics": {"n_trades": n_trades, "avg_net_pct": avg_net_pct},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    derived = root / "state" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "paper_signal_training.jsonl").write_text(
        json.dumps({"training_row_id": "training_1", "candidate_id": "candidate_1"}) + "\n",
        encoding="utf-8",
    )
    db = FarmTasksDB(tasks_db_path(root), clock=lambda: 1.0)
    task_id, _ = db.enqueue_task(
        task_type="run_sweep",
        task_key="retest_1",
        symbol="BTC_USDT_SWAP",
        timeframe="1h",
        family="momentum_breakout",
        source_event_id="ort_1",
        payload=context,
        now=1.0,
    )
    db.complete_task(task_id, run_dir_label=run_label, last_result_ref=run_label, now=2.0)
    db.close()


def test_completed_retest_returns_to_review_and_candidate_memory(tmp_path):
    _completed_retest(tmp_path)

    summary = build_outcome_retest_results(tmp_path)

    assert summary["schema"] == "outcome_retest_results.v1"
    assert summary["results"] == 1
    assert summary["by_verdict"] == {"selection_only": 1}
    row = summary["items"][0]
    assert row["retest_id"] == "ort_1"
    assert row["review_id"] == "review_1"
    assert row["source_candidate_id"] == "candidate_1"
    assert "baseline_net_pct" not in row
    assert "delta_vs_baseline_pct" not in row
    assert row["best_n_trades"] == 20
    assert row["evidence_stage"] == "selection"
    assert row["required_evaluation"] == "untouched_out_of_sample"
    assert row["untouched_evaluation_required"] is True
    assert row["selection_window_start"] == "2026-01-01T00:00:00+00:00"
    assert row["selection_window_end"] == "2026-02-01T00:00:00+00:00"
    assert row["evaluated_at"] == "1970-01-01T00:00:02+00:00"
    assert row["paper_only"] is True
    assert row["execution_allowed"] is False


def test_completed_retest_requires_minimum_evidence(tmp_path):
    _completed_retest(tmp_path, n_trades=2, avg_net_pct=1.0)

    summary = build_outcome_retest_results(tmp_path)

    assert summary["by_verdict"] == {"insufficient_evidence": 1}


def test_outcome_retest_result_has_no_live_order_provider_or_sender_imports():
    tree = ast.parse(Path("src/research_lab/outcome_retest_result.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("src.exchange", "src.utils.telegram", "dotenv", "requests", "aiohttp")
    assert not any(name.startswith(forbidden) for name in imported)
