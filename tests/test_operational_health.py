import json
import os
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
    llm_boundaries = report["llm_surface_boundaries"]
    assert llm_boundaries["scanner_uses_llm_provider_env"] is True
    assert llm_boundaries["scanner_supports_alibaba"] is True
    assert llm_boundaries["telegram_chart_formatter_provider"] == "yandex_only"
    formatter_status = llm_boundaries["telegram_chart_formatter_status"]
    assert formatter_status["schema"] == "llm_formatter_provider.v1"
    assert formatter_status["provider"] == "yandex"
    assert formatter_status["provider_scope"] == "yandex_only"
    assert isinstance(formatter_status["api_key_set"], bool)
    assert isinstance(formatter_status["folder_id_set"], bool)
    assert formatter_status["configured"] == (
        formatter_status["api_key_set"] and formatter_status["folder_id_set"]
    )
    assert formatter_status["telegram_send_authority"] is False
    assert formatter_status["execution_authority"] is False
    assert "qwen3-235b" in formatter_status["model_label"]
    assert "b1git" not in str(formatter_status)
    assert llm_boundaries["telegram_chart_formatter_configured"] == formatter_status["configured"]
    assert llm_boundaries["telegram_chart_formatter_uses_llm_provider_env"] is False
    assert llm_boundaries["telegram_chart_formatter_uses_budget_guard"] is True
    assert llm_boundaries["telegram_chart_formatter_prompt_integrity"] is True
    assert llm_boundaries["telegram_chart_formatter_mojibake_detected"] is False
    assert llm_boundaries["scanner_formatter_provider_mismatch"] is True
    assert llm_boundaries["analyze_chart_can_send_telegram"] is True
    assert llm_boundaries["analyze_chart_send_default"] is False
    text_quality = report["legacy_product_text_quality"]
    assert text_quality["schema"] == "legacy_product_text_quality.v1"
    assert text_quality["files_scanned"] >= 5
    assert text_quality["clean"] is True
    assert text_quality["files_with_markers"] == 0
    assert report["readiness"]["legacy_product_text_quality"]["status"] == "pass"
    analyzer = report["product_analyzer_boundary"]
    assert analyzer["analyze_chart_imports_okx_client"] is True
    assert analyzer["analyze_chart_reads_okx_credentials"] is True
    assert analyzer["analyze_chart_uses_llm_formatter"] is True
    assert analyzer["analyze_chart_can_send_telegram"] is True
    assert analyzer["analyze_chart_send_default"] is False
    assert analyzer["analyze_chart_imports_auto_execute"] is False
    assert analyzer["run_latest_analysis_interactive"] is True
    assert analyzer["run_latest_analysis_wraps_analyze_chart"] is True
    assert analyzer["run_latest_analysis_imports_auto_execute"] is True
    assert analyzer["run_latest_analysis_auto_trade_guarded"] is True
    assert analyzer["run_latest_analysis_requires_auto_execute_opt_in"] is True
    assert analyzer["safe_for_farm_pfr_runtime"] is False
    training = report["training_data"]["paper_signal_training"]
    assert training["rows"] == 0
    assert training["schema_rows"] == 0
    assert report["main_bridge"]["orders_enabled_by_bridge"] is False
    assert report["readiness"]["auto_trade_off"]["status"] == "pass"
    assert report["readiness"]["canonical_launch_surface"]["status"] == "pass"
    assert report["readiness"]["legacy_live_runtime_isolated"]["status"] == "pass"
    assert report["readiness"]["legacy_loop_guards"]["status"] == "pass"
    assert report["launch_surfaces"]["control_room"]["current"] is True
    assert report["launch_surfaces"]["farm_full_cycle_loop"]["current"] is True
    assert report["launch_surfaces"]["old_main_py"]["current"] is False
    assert report["paper_data_flow"]["old_main_py_consumes_farm_pfr"] is False
    assert report["paper_data_flow"]["execution_allowed"] is False
    assert report["paper_data_flow"]["telegram_send_default"] is False
    assert report["telegram_delivery_flow"]["farm_core_sends_telegram"] is False
    assert report["telegram_delivery_flow"]["paper_sends_telegram_by_default"] is False
    assert report["telegram_delivery_flow"]["execution_authority"] is False
    assert report["telegram_delivery_flow"]["telegram_analyzer_current_for_farm"] is False
    assert report["telegram_delivery_flow"]["telegram_analyzer_imports_auto_execute"] is True
    assert report["telegram_delivery_flow"]["telegram_analyzer_auto_trade_guarded"] is True
    assert report["telegram_delivery_flow"]["telegram_analyzer_requires_auto_execute_opt_in"] is True
    main_boundary = report["main_engine_boundary"]
    assert main_boundary["order_capable"] is True
    assert main_boundary["sets_leverage"] is True
    assert main_boundary["imports_private_okx_client"] is True
    assert main_boundary["consumes_farm_tasks_db"] is False
    assert main_boundary["consumes_strategy_lab_db"] is False
    assert main_boundary["consumes_main_paper_queue"] is False
    assert main_boundary["safe_to_use_as_paper_executor"] is False
    assert report["readiness"]["main_paper_consumer_available"]["status"] == "warn"
    assert report["readiness"]["main_paper_runtime_queue_available"]["status"] == "warn"
    assert report["readiness"]["main_paper_runtime_observation_available"]["status"] == "warn"
    assert report["readiness"]["paper_chain_counts"]["status"] == "warn"
    assert report["readiness"]["paper_runtime_observed"]["status"] == "warn"
    assert report["readiness"]["main_runtime_consumer"]["status"] == "planned"
    assert report["readiness"]["ready_for_visible_paper_research_loop"]["status"] == "warn"
    assert report["readiness"]["paper_telegram_preview_available"]["status"] == "warn"
    assert report["readiness"]["paper_telegram_sender_available"]["status"] == "warn"
    assert report["readiness"]["telegram_delivery_ownership"]["status"] == "pass"
    assert report["readiness"]["telegram_analyzer_llm_provider_review"]["status"] == "warn"
    assert report["readiness"]["product_analyzer_prompt_integrity"]["status"] == "pass"
    assert report["readiness"]["legacy_product_text_quality"]["status"] == "pass"
    assert report["readiness"]["manual_product_analyzer_boundary"]["status"] == "warn"
    assert report["readiness"]["paper_signal_training_export"]["status"] == "warn"
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
    training.write_text(
        json.dumps({"schema": "PaperSignalTrainingRow.v1", "paper_only": True}) + "\n",
        encoding="utf-8",
    )

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
    assert report["readiness"]["paper_telegram_sender_available"]["status"] == "warn"
    assert report["readiness"]["training_data_exports"]["status"] == "pass"
    assert report["training_data"]["paper_signal_training"]["rows"] == 1
    assert report["training_data"]["paper_signal_training"]["schema_rows"] == 1
    assert report["training_data"]["paper_signal_training"]["paper_only_false"] == 0
    assert report["readiness"]["paper_signal_training_export"]["status"] == "pass"
    assert Path(report["pfr"]["db"]["path"]) == pfr


def test_operational_health_rejects_mixed_training_schema(tmp_path, monkeypatch):
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    (derived / "paper_signal_training.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"schema": "PaperSignalTrainingRow.v1", "paper_only": True}),
                json.dumps({"schema": "OtherSchema.v1", "paper_only": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("AUTO_TRADE", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    training = report["training_data"]["paper_signal_training"]
    assert training["rows"] == 2
    assert training["schema_rows"] == 1
    assert report["readiness"]["paper_signal_training_export"]["status"] == "warn"


def test_operational_health_warns_when_paper_training_export_is_stale(tmp_path, monkeypatch):
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    paper = derived / "paper_signals.jsonl"
    training = derived / "paper_signal_training.jsonl"
    paper.write_text(json.dumps({"schema": "PaperActionSignal.v1"}) + "\n", encoding="utf-8")
    training.write_text(
        json.dumps({"schema": "PaperSignalTrainingRow.v1", "paper_only": True}) + "\n",
        encoding="utf-8",
    )
    os.utime(training, (1000, 1000))
    os.utime(paper, (1005, 1005))

    monkeypatch.delenv("AUTO_TRADE", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    freshness = report["training_data"]["paper_signal_training_freshness"]
    assert freshness["stale_vs_source"] is True
    assert freshness["age_delta_seconds"] == 5.0
    assert report["readiness"]["paper_signal_training_export"]["status"] == "warn"


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
    assert surfaces["manual_chart_analyzer"]["current"] is False
    assert surfaces["manual_latest_analysis"]["current"] is False
    assert surfaces["legacy_product_stack"]["current"] is False
    assert surfaces["scanner_runtime"]["current"] is True
    assert surfaces["legacy_ws_scanner"]["current"] is False
    assert surfaces["old_main_py"]["current"] is False
    assert "must remain isolated" in surfaces["old_main_py"]["boundary"]
    assert flow["current_owner"] == "scripts.strategy_lab.farm_loop with --run-paper-signals"
    assert "PFR database seeding, bounded and scanned after live movers" in flow["selection_priority"]
    assert flow["old_main_py_consumes_farm_pfr"] is False
    assert report["main_engine_boundary"]["replacement_path"] == "src.research_lab.main_paper_runtime"
    assert report["legacy_loop_boundaries"]["scanner_farm_loop_current"] is False
    assert report["legacy_loop_boundaries"]["scanner_farm_loop_has_abort_guard"] is True
    assert report["legacy_loop_boundaries"]["universe_farm_loop_current"] is False
    assert report["legacy_loop_boundaries"]["universe_farm_loop_has_abort_guard"] is True
    assert report["legacy_loop_boundaries"]["canonical_replacement"] == "scripts.strategy_lab.farm_loop"
    assert report["readiness"]["canonical_launch_surface"]["status"] == "pass"
    assert report["readiness"]["legacy_live_runtime_isolated"]["status"] == "pass"
    assert report["readiness"]["legacy_loop_guards"]["status"] == "pass"
    assert report["readiness"]["telegram_delivery_ownership"]["status"] == "pass"


def test_operational_health_documents_telegram_delivery_ownership(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")
    delivery = report["telegram_delivery_flow"]

    assert delivery["schema"] == "telegram_delivery_flow.v1"
    assert delivery["farm_core_sends_telegram"] is False
    assert delivery["paper_sends_telegram_by_default"] is False
    assert delivery["paper_sender_cli"] == "scripts.strategy_lab.paper_telegram_sender"
    assert delivery["paper_sender_chat_env"] == "PAPER_CHAT_ID"
    assert delivery["scanner_surface_sends_to_subscribers"] is True
    assert delivery["telegram_analyzer_current_for_farm"] is False
    assert delivery["telegram_analyzer_imports_auto_execute"] is True
    assert delivery["telegram_analyzer_auto_trade_guarded"] is True
    assert delivery["telegram_analyzer_requires_auto_execute_opt_in"] is True
    assert delivery["legacy_ws_scanner_uses_okx_client"] is True
    assert delivery["secrets_printed"] is False
    assert delivery["execution_authority"] is False
    assert "llm_client" in delivery["scanner_provider_path"]
    assert "llm_formatter" in delivery["chart_formatter_path"]
    assert report["readiness"]["telegram_delivery_ownership"]["status"] == "pass"
    assert report["readiness"]["telegram_analyzer_execution_boundary"]["status"] == "pass"


def test_operational_health_reports_product_analyzer_shared_router_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCT_ANALYZER_LLM_ROUTER", "llm_client")
    monkeypatch.setenv("LLM_PROVIDER", "alibaba")
    monkeypatch.setenv("ALIBABA_API_KEY", "secret-alibaba")
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")
    llm_boundaries = report["llm_surface_boundaries"]
    formatter_status = llm_boundaries["telegram_chart_formatter_status"]
    rendered = str(report)

    assert formatter_status["provider"] == "alibaba"
    assert formatter_status["provider_scope"] == "shared_llm_client_opt_in"
    assert formatter_status["shared_router_active"] is True
    assert formatter_status["follows_llm_provider_env"] is True
    assert formatter_status["configured"] is True
    assert llm_boundaries["telegram_chart_formatter_provider"] == "shared_llm_client_opt_in"
    assert llm_boundaries["telegram_chart_formatter_uses_llm_provider_env"] is True
    assert llm_boundaries["scanner_formatter_provider_mismatch"] is False
    assert report["readiness"]["telegram_analyzer_llm_provider_review"]["status"] == "pass"
    assert "secret-alibaba" not in rendered


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


def test_operational_health_reports_paper_telegram_sender(tmp_path, monkeypatch):
    delivery = tmp_path / "state" / "derived" / "paper_telegram_delivery.json"
    delivery.parent.mkdir(parents=True)
    delivery.write_text(
        json.dumps({"records_read": 2, "eligible": 2, "sent": 0, "skipped": 2, "errors": 0}),
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["journals"]["paper_telegram_delivery_snapshot"]["exists"] is True
    assert report["paper_chain"]["telegram_delivery"]["eligible"] == 2
    assert report["paper_chain"]["telegram_delivery"]["skipped"] == 2
    assert report["readiness"]["paper_telegram_sender_available"]["status"] == "pass"


def test_operational_health_reports_complete_paper_chain_counts(tmp_path, monkeypatch):
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    pfr = tmp_path / "state" / "strategy_lab.sqlite"
    pfr.parent.mkdir(parents=True, exist_ok=True)
    pfr.write_bytes(b"sqlite")
    (derived / "paper_signal_training.jsonl").write_text(
        json.dumps({"schema": "PaperSignalTrainingRow.v1", "paper_only": True}) + "\n",
        encoding="utf-8",
    )
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

    report = H.collect(private_root=tmp_path, pfr_db_path=pfr)

    assert report["paper_chain"]["instructions"]["instructions"] == 2
    assert report["paper_chain"]["consumer"]["accepted"] == 2
    assert report["paper_chain"]["runtime_queue"]["queued"] == 2
    assert report["paper_chain"]["runtime_observation"]["observed"] == 2
    assert report["paper_chain"]["telegram_preview"]["rendered"] == 2
    assert report["readiness"]["paper_chain_counts"]["status"] == "pass"
    assert report["readiness"]["paper_runtime_observed"]["status"] == "pass"
    assert report["readiness"]["training_data_exports"]["status"] == "pass"
    assert report["readiness"]["paper_signal_training_export"]["status"] == "pass"
    assert report["readiness"]["ready_for_visible_paper_research_loop"]["status"] == "pass"


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
