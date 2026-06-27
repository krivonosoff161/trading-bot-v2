import json
from pathlib import Path

from scripts.strategy_lab import operational_health as H


def test_operational_health_does_not_expose_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("PAPER_CHAT_ID", "111")
    monkeypatch.setenv("ALIBABA_API_KEY", "secret-alibaba")
    monkeypatch.setenv("LLM_PROVIDER", "alibaba")
    monkeypatch.delenv("STRATEGY_LAB_LLM_ENABLED", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")
    rendered = str(report)

    assert report["telegram"]["paper"]["configured"] is True
    assert report["scanner_llm"]["alibaba_key_set"] is True
    assert report["main_bridge"]["orders_enabled_by_bridge"] is False
    assert report["readiness"]["auto_trade_off"]["status"] == "pass"
    assert report["readiness"]["canonical_launch_surface"]["status"] == "pass"
    assert report["readiness"]["legacy_live_runtime_isolated"]["status"] == "pass"
    assert report["launch_surfaces"]["control_room"]["current"] is True
    assert report["launch_surfaces"]["farm_full_cycle_loop"]["current"] is True
    assert report["launch_surfaces"]["old_main_py"]["current"] is False
    assert report["paper_data_flow"]["old_main_py_consumes_farm_pfr"] is False
    assert report["paper_data_flow"]["execution_allowed"] is False
    assert report["paper_data_flow"]["telegram_send_default"] is False
    assert report["telegram_delivery_flow"]["farm_core_sends_telegram"] is False
    assert report["telegram_delivery_flow"]["paper_sends_telegram_by_default"] is False
    assert report["telegram_delivery_flow"]["execution_authority"] is False
    assert report["readiness"]["main_paper_consumer_available"]["status"] == "warn"
    assert report["readiness"]["main_paper_runtime_queue_available"]["status"] == "warn"
    assert report["readiness"]["main_paper_runtime_observation_available"]["status"] == "warn"
    assert report["readiness"]["paper_chain_counts"]["status"] == "warn"
    assert report["readiness"]["paper_runtime_observed"]["status"] == "warn"
    assert report["readiness"]["main_runtime_consumer"]["status"] == "planned"
    assert report["readiness"]["paper_telegram_preview_available"]["status"] == "warn"
    assert report["readiness"]["telegram_delivery_ownership"]["status"] == "pass"
    assert "secret-token" not in rendered
    assert "secret-alibaba" not in rendered


def test_operational_health_reports_existing_journal_files(tmp_path, monkeypatch):
    pfr = tmp_path / "state" / "strategy_lab.sqlite"
    pfr.parent.mkdir(parents=True)
    pfr.write_bytes(b"sqlite")
    paper = tmp_path / "state" / "derived" / "paper_signals.jsonl"
    paper.parent.mkdir(parents=True)
    paper.write_text("{}", encoding="utf-8")
    training = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    training.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=pfr)

    assert report["pfr"]["db"]["exists"] is True
    assert report["journals"]["paper_signals"]["exists"] is True
    assert report["main_bridge"]["status"] == "not_connected"
    assert report["main_bridge"]["paper_sources_ready"] is True
    assert report["readiness"]["pfr_source_available"]["status"] == "pass"
    assert report["readiness"]["paper_signal_store_available"]["status"] == "pass"
    assert report["readiness"]["main_instruction_view_available"]["status"] == "warn"
    assert report["readiness"]["main_paper_consumer_available"]["status"] == "warn"
    assert report["readiness"]["main_paper_runtime_queue_available"]["status"] == "warn"
    assert report["readiness"]["main_paper_runtime_observation_available"]["status"] == "warn"
    assert report["readiness"]["paper_telegram_preview_available"]["status"] == "warn"
    assert report["readiness"]["training_data_exports"]["status"] == "pass"
    assert Path(report["pfr"]["db"]["path"]) == pfr


def test_operational_health_documents_launch_surface_ownership(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")
    surfaces = report["launch_surfaces"]
    flow = report["paper_data_flow"]

    assert surfaces["control_room"]["exists"] is True
    assert "canonical visible operator entrypoint" in surfaces["control_room"]["role"]
    assert surfaces["farm_full_cycle_loop"]["exists"] is True
    assert surfaces["farm_full_cycle_loop"]["current"] is True
    assert surfaces["strategy_lab_start_legacy"]["current"] is False
    assert surfaces["telegram_analyzer_start"]["current"] is False
    assert surfaces["legacy_product_stack"]["current"] is False
    assert surfaces["scanner_runtime"]["current"] is True
    assert surfaces["legacy_ws_scanner"]["current"] is False
    assert surfaces["old_main_py"]["current"] is False
    assert "must remain isolated" in surfaces["old_main_py"]["boundary"]
    assert flow["current_owner"] == "scripts.strategy_lab.farm_loop with --run-paper-signals"
    assert "PFR database seeding, bounded and scanned after live movers" in flow["selection_priority"]
    assert flow["old_main_py_consumes_farm_pfr"] is False
    assert report["readiness"]["canonical_launch_surface"]["status"] == "pass"
    assert report["readiness"]["legacy_live_runtime_isolated"]["status"] == "pass"
    assert report["readiness"]["telegram_delivery_ownership"]["status"] == "pass"


def test_operational_health_documents_telegram_delivery_ownership(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")
    delivery = report["telegram_delivery_flow"]

    assert delivery["schema"] == "telegram_delivery_flow.v1"
    assert delivery["farm_core_sends_telegram"] is False
    assert delivery["paper_sends_telegram_by_default"] is False
    assert delivery["scanner_surface_sends_to_subscribers"] is True
    assert delivery["legacy_ws_scanner_uses_okx_client"] is True
    assert delivery["secrets_printed"] is False
    assert delivery["execution_authority"] is False
    assert "llm_client" in delivery["scanner_provider_path"]
    assert "llm_formatter" in delivery["chart_formatter_path"]
    assert report["readiness"]["telegram_delivery_ownership"]["status"] == "pass"


def test_operational_health_reports_main_instruction_view(tmp_path, monkeypatch):
    view = tmp_path / "state" / "derived" / "main_paper_instructions.json"
    view.parent.mkdir(parents=True)
    view.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["main_bridge"]["instruction_view_exists"] is True
    assert report["main_bridge"]["status"] == "instruction_view_ready"
    assert report["main_bridge"]["orders_enabled_by_bridge"] is False
    assert report["readiness"]["main_instruction_view_available"]["status"] == "pass"


def test_operational_health_reports_main_paper_consumer_view(tmp_path, monkeypatch):
    view = tmp_path / "state" / "derived" / "main_paper_consumed.json"
    view.parent.mkdir(parents=True)
    view.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["main_bridge"]["consumer_view_exists"] is True
    assert report["main_bridge"]["status"] == "consumer_audit_ready"
    assert report["main_bridge"]["orders_enabled_by_bridge"] is False
    assert report["readiness"]["main_paper_consumer_available"]["status"] == "pass"
    assert report["readiness"]["main_runtime_consumer"]["status"] == "planned"


def test_operational_health_reports_main_paper_runtime_queue(tmp_path, monkeypatch):
    view = tmp_path / "state" / "derived" / "main_paper_runtime_queue.json"
    view.parent.mkdir(parents=True)
    view.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["main_bridge"]["runtime_queue_exists"] is True
    assert report["main_bridge"]["status"] == "runtime_queue_ready"
    assert report["readiness"]["main_paper_runtime_queue_available"]["status"] == "pass"
    assert report["readiness"]["main_runtime_consumer"]["status"] == "planned"


def test_operational_health_reports_main_paper_runtime_observation(tmp_path, monkeypatch):
    view = tmp_path / "state" / "derived" / "main_paper_runtime_observation.json"
    view.parent.mkdir(parents=True)
    view.write_text(
        json.dumps({"rows_read": 2, "observed": 2, "reviewed": 1, "pending": 1, "invalid": 0, "provider_error": 0}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["main_bridge"]["runtime_observation_exists"] is True
    assert report["main_bridge"]["status"] == "paper_runtime_observed"
    assert report["paper_chain"]["runtime_observation"]["observed"] == 2
    assert report["readiness"]["main_paper_runtime_observation_available"]["status"] == "pass"
    assert report["readiness"]["paper_runtime_observed"]["status"] == "pass"


def test_operational_health_reports_paper_telegram_preview(tmp_path, monkeypatch):
    preview = tmp_path / "state" / "derived" / "paper_telegram_preview.json"
    preview.parent.mkdir(parents=True)
    preview.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["journals"]["paper_telegram_preview_snapshot"]["exists"] is True
    assert report["readiness"]["paper_telegram_preview_available"]["status"] == "pass"


def test_operational_health_reports_complete_paper_chain_counts(tmp_path, monkeypatch):
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    (derived / "main_paper_instructions.json").write_text(
        json.dumps({"instructions": 2, "items": [{}, {}]}),
        encoding="utf-8",
    )
    (derived / "main_paper_consumed.json").write_text(
        json.dumps({"instructions_read": 2, "accepted": 2, "rejected": 0, "items": [{}, {}]}),
        encoding="utf-8",
    )
    (derived / "main_paper_runtime_queue.json").write_text(
        json.dumps({"rows_read": 2, "queued": 2, "invalid": 0, "items": [{}, {}]}),
        encoding="utf-8",
    )
    (derived / "main_paper_runtime_observation.json").write_text(
        json.dumps({"rows_read": 2, "observed": 2, "reviewed": 1, "pending": 1, "invalid": 0, "provider_error": 0}),
        encoding="utf-8",
    )
    (derived / "paper_telegram_preview.json").write_text(
        json.dumps({"records_read": 2, "rendered": 2, "invalid": 0, "items": [{}, {}]}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["paper_chain"]["instructions"]["instructions"] == 2
    assert report["paper_chain"]["consumer"]["accepted"] == 2
    assert report["paper_chain"]["runtime_queue"]["queued"] == 2
    assert report["paper_chain"]["runtime_observation"]["observed"] == 2
    assert report["paper_chain"]["telegram_preview"]["rendered"] == 2
    assert report["readiness"]["paper_chain_counts"]["status"] == "pass"
    assert report["readiness"]["paper_runtime_observed"]["status"] == "pass"


def test_operational_health_blocks_enabled_unconfigured_lab_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "alibaba")
    monkeypatch.setenv("STRATEGY_LAB_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_MODEL_CHEAP", "qwen-test")
    monkeypatch.delenv("STRATEGY_LAB_LLM_API_KEY", raising=False)
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    gate = report["readiness"]["strategy_lab_llm_policy"]
    assert gate["status"] == "blocked"
    assert "provider is not configured" in gate["message"]
