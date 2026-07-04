import argparse
import json
from pathlib import Path

from scripts.strategy_lab import paper_research_e2e_smoke as smoke


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        private_root=tmp_path,
        pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite",
        backend="auto",
        provider="okx-public",
        max_plan_events=2,
        max_prepares=1,
        max_enrich=1,
        max_sweeps=1,
        max_worker_jobs=1,
        max_validations=3,
        max_paper_cards=20,
        data_days=30,
        paper_signals_max_observe=10,
        paper_signals_max_pfr_scan=10,
        paper_signals_pfr_reserved=1,
        main_paper_runtime_limit=30,
        no_discovery_refresh=True,
        run_calculator_advisor=True,
        calculator_provider="ollama",
        calculator_model="calculator",
        calculator_base_url="http://127.0.0.1:11434/v1",
        calculator_timeout=30.0,
        calculator_advisor_max_calls=1,
    )


def test_e2e_smoke_command_runs_full_paper_research_cycle(tmp_path):
    cmd = smoke.build_farm_command(_args(tmp_path))
    text = " ".join(cmd)

    assert "--run-worker" in cmd
    assert "--run-validation" in cmd
    assert "--run-paper" in cmd
    assert "--run-paper-signals" in cmd
    assert "--run-calculator-advisor" in cmd
    assert "--calculator-provider ollama" in text
    assert "--private-root" in cmd
    assert "AUTO_TRADE" not in text


def test_e2e_smoke_verifies_private_artifact_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(
        smoke,
        "collect_health",
        lambda **_kwargs: {"operator_next_actions": {"blocking": []}},
    )
    lineage = tmp_path / "state" / "lineage"
    derived = tmp_path / "state" / "derived"
    advice_dir = tmp_path / "state" / "llm_advice"
    lineage.mkdir(parents=True)
    derived.mkdir(parents=True)
    advice_dir.mkdir(parents=True)
    for name in ("scanner_events", "data_packets", "feature_packets", "cycle_links"):
        (lineage / f"{name}.jsonl").write_text(json.dumps({"schema": name}) + "\n", encoding="utf-8")
    (advice_dir / "calculator_advice.jsonl").write_text(
        json.dumps({"schema": "CalculatorAdvice.v1", "accepted": True}) + "\n",
        encoding="utf-8",
    )
    (derived / "paper_signals.jsonl").write_text(json.dumps({"schema": "PaperActionSignal.v1"}) + "\n", encoding="utf-8")
    for filename in (
        "main_paper_instructions.json",
        "main_paper_consumed.json",
        "main_paper_runtime_queue.json",
        "main_paper_runtime_observation.json",
        "paper_telegram_delivery.json",
    ):
        (derived / filename).write_text(json.dumps({"items": [{"id": "x"}]}), encoding="utf-8")
    (derived / "paper_telegram_preview.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "text": "<b>Paper-сетап: BTC · 15m · long</b>\n"
                        "<i>Автоисполнение выключено.</i>"
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (derived / "paper_signal_training.jsonl").write_text(
        json.dumps({"schema": "TrainingRow.v2", "paper_only": True, "execution_allowed": False}) + "\n",
        encoding="utf-8",
    )

    report = smoke.verify_cycle(tmp_path, require_calculator_accepted=True)

    assert report["ok"] is True
    assert report["execution_allowed"] is False
