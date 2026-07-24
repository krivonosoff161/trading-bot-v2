import json
import os
from pathlib import Path

from scripts.strategy_lab import operational_health as H
from src.research_lab.paper_signals.contract import PaperActionSignal
from src.research_lab.paper_signals.store import append_signal, update_signal
from src.research_lab.paper_signals.training_export import export_training_rows
from src.research_lab.product_signal_training import export_product_signal_training


def _paper_signal(signal_id: str, *, status: str) -> PaperActionSignal:
    return PaperActionSignal(
        signal_id=signal_id,
        source="farm",
        symbol="A_USDT_SWAP",
        okx_inst_id="A-USDT-SWAP",
        timeframe="15m",
        side="long",
        setup_family="early_tp_tactical",
        entry_zone=[100.0, 101.0],
        stop_loss=98.0,
        invalidation_rule="close below local support",
        take_profit_plan=[{"label": "tp1", "price": 105.0, "size_frac": 0.5}],
        max_hold_bars=12,
        max_hold_minutes=180,
        reason_now="fresh pullback",
        status=status,
        created_at=1000.0,
        expires_at=2000.0,
        ref_price=100.5,
        risk_pct=2.5,
        boundary_ts=900,
        data_fingerprint=f"fp_{signal_id}",
        dedup_key=f"A|15m|{signal_id}",
        validator_context={
            "ready_strategy_id": "ready_1",
            "source_validation_verdict": "PAPER_FORWARD_READY",
        },
    )


def test_operational_health_does_not_expose_secret_values(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setattr(H, "list_delivery_users", lambda: [{"chat_id": "111", "status": "active"}])
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
    assert llm_boundaries["telegram_chart_formatter_provider"] == "shared_llm_client_opt_in"
    formatter_status = llm_boundaries["telegram_chart_formatter_status"]
    assert formatter_status["schema"] == "llm_formatter_provider.v1"
    assert formatter_status["provider"] == "alibaba"
    assert formatter_status["provider_scope"] == "shared_llm_client_opt_in"
    assert isinstance(formatter_status["api_key_set"], bool)
    assert isinstance(formatter_status["folder_id_set"], bool)
    assert formatter_status["configured"] == formatter_status["api_key_set"]
    assert formatter_status["telegram_send_authority"] is False
    assert formatter_status["execution_authority"] is False
    assert "qwen3-235b" in formatter_status["model_label"]
    assert "b1git" not in str(formatter_status)
    assert llm_boundaries["telegram_chart_formatter_configured"] == formatter_status["configured"]
    assert llm_boundaries["telegram_chart_formatter_uses_llm_provider_env"] is True
    assert llm_boundaries["telegram_chart_formatter_launcher_sets_shared_router"] is True
    assert llm_boundaries["telegram_chart_formatter_effective_shared_router"] is True
    assert llm_boundaries["telegram_chart_formatter_effective_provider"] == "alibaba"
    assert llm_boundaries["telegram_chart_formatter_effective_provider_scope"] == "shared_llm_client_opt_in"
    assert llm_boundaries["telegram_chart_formatter_effective_shared_entrypoints"] == [
        "generate_client_text",
        "generate_edu_text",
    ]
    assert llm_boundaries["telegram_chart_formatter_uses_budget_guard"] is True
    assert llm_boundaries["telegram_chart_formatter_prompt_integrity"] is True
    assert llm_boundaries["telegram_chart_formatter_mojibake_detected"] is False
    assert llm_boundaries["scanner_formatter_provider_mismatch"] is False
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
    launch_contract = report["product_analyzer_launch_contract"]
    assert launch_contract["schema"] == "product_analyzer_launch_contract.v1"
    assert launch_contract["canonical_paper_launcher"] == "bat/strategy_lab_farm_full_cycle_loop.bat"
    assert launch_contract["canonical_farm_module"] == "scripts.strategy_lab.farm_loop"
    assert launch_contract["canonical_requires_run_paper_signals"] is True
    assert launch_contract["manual_telegram_launcher"] == "start.bat"
    assert launch_contract["manual_telegram_current_for_farm"] is False
    assert launch_contract["telegram_bot_main_starts_scanner_loop"] is False
    assert launch_contract["telegram_bot_main_polls_updates"] is True
    assert launch_contract["telegram_bot_auto_execute_opt_in"] is True
    assert launch_contract["manual_chart_send_default"] is False
    assert launch_contract["manual_chart_can_send_with_flag"] is True
    assert launch_contract["manual_chart_uses_private_okx_client"] is True
    assert launch_contract["manual_latest_requires_human_prompt"] is True
    assert launch_contract["manual_latest_auto_execute_import_gated"] is True
    assert launch_contract["text_card_shared_router_entrypoint"] == "generate_client_text"
    assert launch_contract["educational_qa_shared_router_entrypoint"] == "generate_edu_text"
    assert launch_contract["shared_router_opt_in_env"] == "PRODUCT_ANALYZER_LLM_ROUTER"
    assert launch_contract["shared_router_active"] is True
    assert launch_contract["start_bat_sets_shared_router"] is True
    assert launch_contract["start_telegram_bot_bat_sets_shared_router"] is True
    assert launch_contract["launcher_sets_shared_router"] is True
    assert launch_contract["effective_shared_router"] is True
    assert launch_contract["effective_provider"] == "alibaba"
    assert launch_contract["premium_vision_provider"] == "alibaba"
    assert launch_contract["premium_vision_configured"] is True
    assert launch_contract["premium_vision_yandex_only"] is False
    assert launch_contract["edu_qa_yandex_only"] is False
    assert launch_contract["edu_qa_shared_router_entrypoint"] is True
    assert launch_contract["farm_pfr_runtime_uses_manual_product_stack"] is False
    assert launch_contract["old_main_consumes_paper_queue"] is False
    assert launch_contract["telegram_send_default"] is False
    assert launch_contract["execution_allowed"] is False
    revival = report["product_analyzer_revival_checklist"]
    assert revival["schema"] == "product_analyzer_revival_checklist.v1"
    assert revival["status"] == "review_required"
    assert revival["canonical_paper_cycle_allowed"] is True
    assert revival["manual_product_alerts_allowed"] is False
    assert revival["live_execution_allowed"] is False
    assert revival["validated"] == {
        "text_prompt_integrity": True,
        "text_prompt_no_mojibake": True,
        "text_cards_use_effective_shared_router": True,
        "scanner_formatter_provider_aligned": True,
        "manual_chart_send_default_off": True,
        "manual_latest_auto_execute_double_gated": True,
        "farm_pfr_does_not_use_manual_product_stack": True,
        "old_main_does_not_consume_paper_queue": True,
        "premium_vision_provider_configured": True,
    }
    assert "premium_vision_provider_and_prompt" not in revival["remaining_review"]
    assert "executor_contract_before_any_old_main_reuse" in revival["remaining_review"]
    assert "paper_telegram_preview" in revival["allowed_next_step"]
    assert "does not prove" in revival["non_claim"]
    assert report["readiness"]["product_analyzer_launch_contract"]["status"] == "pass"
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
    assert report["readiness"]["telegram_analyzer_llm_provider_review"]["status"] == "pass"
    assert report["readiness"]["premium_vision_provider"]["status"] == "pass"
    assert report["readiness"]["product_analyzer_launch_contract"]["status"] == "pass"
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
    assert report["readiness"]["product_signal_event_log"]["status"] == "pass"
    assert Path(report["pfr"]["db"]["path"]) == pfr


def test_operational_health_reports_product_signal_event_safety(tmp_path, monkeypatch):
    events = tmp_path / "logs" / "signals" / "signal_events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps(
            {
                "schema": "signal_event.v1",
                "source": "manual_telegram",
                "paper_only": True,
                "execution_allowed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(H, "ROOT", tmp_path)
    monkeypatch.delenv("AUTO_TRADE", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    product_events = report["training_data"]["product_signal_events"]
    assert product_events["rows"] == 1
    assert product_events["schema_rows"] == 1
    assert product_events["execution_allowed_true"] == 0
    assert report["readiness"]["product_signal_event_log"]["status"] == "pass"
    assert report["readiness"]["product_signal_training_export"]["status"] == "warn"

    export_product_signal_training(tmp_path, source_log=events)
    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    product_training = report["training_data"]["product_signal_training"]
    assert product_training["rows"] == 1
    assert product_training["schema_rows"] == 1
    assert product_training["execution_allowed_true"] == 0
    assert report["journals"]["product_signal_training"]["exists"] is True
    assert report["readiness"]["product_signal_training_export"]["status"] == "pass"

    events.write_text(
        json.dumps(
            {
                "schema": "signal_event.v1",
                "source": "manual_telegram",
                "paper_only": True,
                "execution_allowed": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    assert report["training_data"]["product_signal_events"]["execution_allowed_true"] == 1
    assert report["readiness"]["product_signal_event_log"]["status"] == "warn"


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


def test_operational_health_does_not_mark_training_stale_for_active_paper_updates(tmp_path, monkeypatch):
    terminal = _paper_signal("terminal_1", status="reviewed")
    terminal.outcome = {"result": "take", "net_pct": 1.0}
    terminal.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, terminal)
    summary = export_training_rows(tmp_path)
    assert summary["rows"] == 1

    active = _paper_signal("active_1", status="armed")
    append_signal(tmp_path, active)
    paper = tmp_path / "state" / "derived" / "paper_signals.jsonl"
    training = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    os.utime(training, (1000, 1000))
    os.utime(paper, (1005, 1005))

    monkeypatch.delenv("AUTO_TRADE", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    freshness = report["training_data"]["paper_signal_training_freshness"]
    assert freshness["freshness_mode"] == "terminal_hash"
    assert freshness["source_terminal_rows"] == 1
    assert freshness["snapshot_source_terminal_rows"] == 1
    assert freshness["stale_vs_source"] is False
    assert report["readiness"]["paper_signal_training_export"]["status"] == "pass"


def test_operational_health_treats_recent_terminal_updates_as_in_motion(tmp_path, monkeypatch):
    terminal = _paper_signal("terminal_1", status="reviewed")
    terminal.outcome = {"result": "take", "net_pct": 1.0}
    terminal.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, terminal)
    export_training_rows(tmp_path)

    terminal.outcome = {"result": "stop", "net_pct": -1.0}
    terminal.review = {"diagnosis": "wrong_direction"}
    update_signal(tmp_path, terminal)
    paper = tmp_path / "state" / "derived" / "paper_signals.jsonl"
    training = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    os.utime(training, (1000, 1000))
    os.utime(paper, (1005, 1005))

    monkeypatch.delenv("AUTO_TRADE", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    freshness = report["training_data"]["paper_signal_training_freshness"]
    assert freshness["freshness_mode"] == "terminal_hash"
    assert freshness["terminal_hash_mismatch"] is True
    assert freshness["in_motion"] is True
    assert freshness["stale_vs_source"] is False
    assert report["readiness"]["paper_signal_training_export"]["status"] == "pass"


def test_operational_health_treats_recent_negative_delta_terminal_mismatch_as_in_motion(
    tmp_path, monkeypatch
):
    terminal = _paper_signal("terminal_1", status="reviewed")
    terminal.outcome = {"result": "take", "net_pct": 1.0}
    terminal.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, terminal)
    export_training_rows(tmp_path)

    terminal.outcome = {"result": "stop", "net_pct": -1.0}
    terminal.review = {"diagnosis": "wrong_direction"}
    update_signal(tmp_path, terminal)
    paper = tmp_path / "state" / "derived" / "paper_signals.jsonl"
    training = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    os.utime(paper, (1000, 1000))
    os.utime(training, (1005, 1005))

    monkeypatch.delenv("AUTO_TRADE", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    freshness = report["training_data"]["paper_signal_training_freshness"]
    assert freshness["age_delta_seconds"] == -5.0
    assert freshness["terminal_hash_mismatch"] is True
    assert freshness["in_motion"] is True
    assert freshness["stale_vs_source"] is False
    assert report["readiness"]["paper_signal_training_export"]["status"] == "pass"


def test_operational_health_warns_on_old_terminal_hash_mismatch(tmp_path, monkeypatch):
    terminal = _paper_signal("terminal_1", status="reviewed")
    terminal.outcome = {"result": "take", "net_pct": 1.0}
    terminal.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, terminal)
    export_training_rows(tmp_path)

    terminal.outcome = {"result": "stop", "net_pct": -1.0}
    terminal.review = {"diagnosis": "wrong_direction"}
    update_signal(tmp_path, terminal)
    paper = tmp_path / "state" / "derived" / "paper_signals.jsonl"
    training = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    os.utime(training, (1000, 1000))
    os.utime(paper, (2000, 2000))

    monkeypatch.delenv("AUTO_TRADE", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    freshness = report["training_data"]["paper_signal_training_freshness"]
    assert freshness["terminal_hash_mismatch"] is True
    assert freshness["in_motion"] is False
    assert freshness["stale_vs_source"] is True
    assert report["readiness"]["paper_signal_training_export"]["status"] == "warn"


def test_operational_health_treats_live_farm_loop_terminal_mismatch_as_in_motion(
    tmp_path, monkeypatch
):
    terminal = _paper_signal("terminal_1", status="reviewed")
    terminal.outcome = {"result": "take", "net_pct": 1.0}
    terminal.review = {"diagnosis": "good_signal"}
    append_signal(tmp_path, terminal)
    export_training_rows(tmp_path)

    terminal.outcome = {"result": "stop", "net_pct": -1.0}
    terminal.review = {"diagnosis": "wrong_direction"}
    update_signal(tmp_path, terminal)
    lock = tmp_path / "state" / "farm_loop.lock"
    lock.write_text("12345", encoding="utf-8")
    paper = tmp_path / "state" / "derived" / "paper_signals.jsonl"
    training = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    os.utime(training, (1000, 1000))
    os.utime(paper, (2500, 2500))
    monkeypatch.setattr(H, "_pid_is_alive", lambda pid: pid == 12345)

    monkeypatch.delenv("AUTO_TRADE", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    freshness = report["training_data"]["paper_signal_training_freshness"]
    assert freshness["age_delta_seconds"] == 1500.0
    assert freshness["active_farm_loop"]["pid_alive"] is True
    assert report["active_farm_loop"]["pid"] == 12345
    assert report["active_farm_loop"]["pid_alive"] is True
    assert freshness["terminal_hash_mismatch"] is True
    assert freshness["in_motion"] is True
    assert freshness["stale_vs_source"] is False
    assert report["readiness"]["paper_signal_training_export"]["status"] == "pass"


def test_operational_health_reports_farm_loop_runtime_status(tmp_path, monkeypatch):
    status = tmp_path / "state" / "farm_loop_status.json"
    status.parent.mkdir(parents=True)
    status.write_text(
        json.dumps(
            {
                "schema": "FarmLoopStatus.v1",
                "pid": 12345,
                "stage": "paper_signals",
                "updated_at": 2000.0,
                "cycle_started_at": 1900.0,
                "cycle_age_seconds": 100.0,
                "loop": True,
                "paper_only": True,
                "execution_allowed": False,
                "details": {"timeframes": ["15m", "1h", "4h"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(H.time, "time", lambda: 2010.0)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    runtime = report["farm_loop_runtime_status"]
    assert runtime["exists"] is True
    assert runtime["stage"] == "paper_signals"
    assert runtime["pid"] == 12345
    assert runtime["updated_age_seconds"] == 10.0
    assert runtime["cycle_age_seconds"] == 100.0
    assert runtime["details"] == {"timeframes": ["15m", "1h", "4h"]}
    assert runtime["paper_only"] is True
    assert runtime["execution_allowed"] is False
    gate = report["readiness"]["farm_loop_process_current"]
    assert gate["status"] == "warn"
    assert "not currently running" in gate["message"]


def test_operational_health_reports_fresh_farm_loop_process(tmp_path, monkeypatch):
    lock = tmp_path / "state" / "farm_loop.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("12345", encoding="utf-8")
    status = tmp_path / "state" / "farm_loop_status.json"
    status.write_text(
        json.dumps(
            {
                "schema": "FarmLoopStatus.v1",
                "pid": 12345,
                "stage": "sleep",
                "updated_at": 2000.0,
                "cycle_age_seconds": 10.0,
                "paper_only": True,
                "execution_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(H, "_pid_is_alive", lambda pid: pid == 12345)
    monkeypatch.setattr(H.time, "time", lambda: 2010.0)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    gate = report["readiness"]["farm_loop_process_current"]
    assert gate["status"] == "pass"
    assert "running" in gate["message"]


def test_operational_health_blocks_active_compute_lifecycle_failure(
    tmp_path,
    monkeypatch,
):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "farm_loop.lock").write_text("12345", encoding="utf-8")
    (state / "farm_loop_status.json").write_text(
        json.dumps(
            {
                "schema": "FarmLoopStatus.v1",
                "pid": 12345,
                "stage": "paper_signals",
                "updated_at": 2000.0,
                "cycle_age_seconds": 10.0,
                "paper_only": True,
                "execution_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    (state / "farm_priority_worker_status.json").write_text(
        json.dumps(
            {
                "stage": "worker_failed",
                "updated_at": 2005.0,
                "details": {"owner_id": "must-not-propagate"},
            }
        ),
        encoding="utf-8",
    )
    (state / "worker_status.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "reason_code": "expired_alive_conflict",
                "updated_at": "1970-01-01T00:33:25+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(H, "_pid_is_alive", lambda pid: pid == 12345)
    monkeypatch.setattr(H.time, "time", lambda: 2010.0)

    report = H.collect(
        private_root=tmp_path,
        pfr_db_path=state / "strategy_lab.sqlite",
    )

    assert report["compute_pipeline"]["state"] == "failed"
    assert report["compute_pipeline"]["hard_fail"] is True
    assert "owner_id" not in json.dumps(report["compute_pipeline"])
    gate = report["readiness"]["compute_pipeline_current"]
    assert gate["status"] == "blocked"
    assert "graceful stop" in gate["action"]


def test_operational_health_exposes_stale_farm_loop_process_action(tmp_path, monkeypatch):
    lock = tmp_path / "state" / "farm_loop.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("12345", encoding="utf-8")
    status = tmp_path / "state" / "farm_loop_status.json"
    status.write_text(
        json.dumps(
            {
                "schema": "FarmLoopStatus.v1",
                "pid": 12345,
                "stage": "paper_signals",
                "updated_at": 1000.0,
                "cycle_age_seconds": 10.0,
                "paper_only": True,
                "execution_allowed": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(H, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(H.time, "time", lambda: 5000.0)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "state" / "strategy_lab.sqlite")

    gate = report["readiness"]["farm_loop_process_current"]
    assert gate["status"] == "warn"
    assert "status heartbeat is stale" in gate["message"]
    assert "strategy_lab_farm_full_cycle_loop.bat" in gate["action"]
    assert "farm_loop_process_current" in {
        item["name"] for item in report["operator_next_actions"]["rebuild_actions"]
    }


def test_excel_journal_freshness_tracks_training_export(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    journal = scripts / "journal.xlsx"
    training = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    training.parent.mkdir(parents=True)
    journal.write_bytes(b"xlsx")
    training.write_text(
        json.dumps({"schema": "PaperSignalTrainingRow.v1", "paper_only": True}) + "\n",
        encoding="utf-8",
    )
    os.utime(training, (1000, 1000))
    os.utime(journal, (1005, 1005))

    freshness = H._excel_journal_freshness(tmp_path, training)

    assert freshness["source_exists"] is True
    assert freshness["derived_exists"] is True
    assert freshness["stale_vs_source"] is False
    assert freshness["age_delta_seconds"] == -5.0


def test_excel_journal_freshness_detects_stale_workbook(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    journal = scripts / "journal.xlsx"
    training = tmp_path / "state" / "derived" / "paper_signal_training.jsonl"
    training.parent.mkdir(parents=True)
    journal.write_bytes(b"xlsx")
    training.write_text(
        json.dumps({"schema": "PaperSignalTrainingRow.v1", "paper_only": True}) + "\n",
        encoding="utf-8",
    )
    os.utime(journal, (1000, 1000))
    os.utime(training, (1005, 1005))

    freshness = H._excel_journal_freshness(tmp_path, training)

    assert freshness["source_exists"] is True
    assert freshness["derived_exists"] is True
    assert freshness["stale_vs_source"] is True
    assert freshness["age_delta_seconds"] == 5.0


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


def test_operational_health_reports_paper_source_composition(tmp_path, monkeypatch):
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    (derived / "paper_signals.jsonl").write_text(
        "\n".join(
            [
                json.dumps({
                    "source": "farm",
                    "setup_family": "early_tp_tactical",
                    "status": "armed",
                    "timeframe": "15m",
                }),
                json.dumps({
                    "source": "pfr_farm",
                    "setup_family": "mean_reversion_fade",
                    "status": "opened_paper",
                    "timeframe": "4h",
                }),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (derived / "main_paper_runtime_queue.json").write_text(
        json.dumps({
            "items": [
                {
                    "source": "farm",
                    "setup_family": "early_tp_tactical",
                    "timeframe": "15m",
                    "runtime_action": "watch_paper",
                    "priority": -2,
                },
                {
                    "source": "pfr_farm",
                    "setup_family": "mean_reversion_fade",
                    "timeframe": "4h",
                    "runtime_action": "watch_paper",
                    "priority": 120,
                },
            ]
        }),
        encoding="utf-8",
    )
    (derived / "main_paper_runtime_observation.json").write_text(
        json.dumps({
            "items": [
                {
                    "source": "farm",
                    "setup_family": "early_tp_tactical",
                    "timeframe": "15m",
                    "signal_status": "reviewed",
                },
                {
                    "source": "pfr_farm",
                    "setup_family": "mean_reversion_fade",
                    "timeframe": "4h",
                    "signal_status": "armed",
                },
            ]
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    composition = report["paper_source_composition"]
    assert composition["schema"] == "paper_source_composition.v1"
    assert composition["paper_signals"]["rows"] == 2
    assert composition["paper_signals"]["by"]["source"] == {"farm": 1, "pfr_farm": 1}
    assert composition["paper_signals"]["by"]["setup_family"] == {
        "early_tp_tactical": 1,
        "mean_reversion_fade": 1,
    }
    assert composition["main_runtime_queue"]["items"] == 2
    assert composition["main_runtime_queue"]["by"]["source"] == {"farm": 1, "pfr_farm": 1}
    assert composition["main_runtime_queue"]["by"]["runtime_action"] == {"watch_paper": 2}
    assert composition["main_runtime_queue"]["priority_min"] == -2
    assert composition["main_runtime_queue"]["priority_max"] == 120
    assert composition["main_runtime_observation"]["items"] == 2
    assert composition["main_runtime_observation"]["by"]["source"] == {"farm": 1, "pfr_farm": 1}
    assert composition["main_runtime_observation"]["by"]["signal_status"] == {
        "reviewed": 1,
        "armed": 1,
    }
    assert "source is preserved" in composition["priority_contract"][3]
    assert composition["pfr_activation"]["requires_explicit_db_path"] is True
    assert composition["pfr_activation"]["source_name"] == "pfr_farm"
    assert composition["execution_allowed"] is False
    priority = report["paper_priority_policy"]
    assert priority["schema"] == "paper_priority_policy.v1"
    assert priority["live_mover_lane"]["order"] == 1
    assert priority["live_mover_lane"]["source"] == "paper_signals source=farm"
    assert "generate first" in priority["live_mover_lane"]["rule"]
    assert priority["pfr_lane"]["order"] == 2
    assert priority["pfr_lane"]["source"] == "paper_signals source=pfr_farm"
    assert priority["pfr_lane"]["requires_explicit_db_path"] is True
    assert priority["pfr_lane"]["bounded_scan_default"] == 30
    assert "shared dedup" in priority["pfr_lane"]["rule"]
    assert priority["main_instruction_view"]["active_statuses"] == ["armed", "opened_paper"]
    assert priority["runtime_queue"]["sort_order"] == [
        "family",
        "timeframe",
        "risk",
        "symbol",
        "source_signal_id",
    ]
    assert priority["old_main_py_consumer"] is False
    assert priority["execution_allowed"] is False


def test_operational_health_documents_telegram_delivery_ownership(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")
    delivery = report["telegram_delivery_flow"]

    assert delivery["schema"] == "telegram_delivery_flow.v1"
    assert delivery["farm_core_sends_telegram"] is False
    assert delivery["paper_sends_telegram_by_default"] is False
    assert delivery["paper_sender_cli"] == "scripts.strategy_lab.paper_telegram_sender"
    assert delivery["paper_sender_chat_env"] == "SUBSCRIPTION_USERS"
    assert delivery["paper_delivery_target"] == "active_subscription_users"
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
    assert "shared-router" in delivery["chart_formatter_path"]
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
    assert report["product_analyzer_launch_contract"]["shared_router_active"] is True
    assert report["product_analyzer_launch_contract"]["launcher_sets_shared_router"] is True
    assert report["product_analyzer_launch_contract"]["effective_shared_router"] is True
    assert report["product_analyzer_launch_contract"]["effective_provider"] == "alibaba"
    assert report["product_analyzer_launch_contract"]["premium_vision_provider"] == "alibaba"
    assert report["product_analyzer_launch_contract"]["premium_vision_configured"] is True
    assert report["product_analyzer_launch_contract"]["premium_vision_yandex_only"] is False
    assert report["product_analyzer_launch_contract"]["edu_qa_yandex_only"] is False
    assert report["product_analyzer_launch_contract"]["edu_qa_shared_router_entrypoint"] is True
    assert llm_boundaries["telegram_chart_formatter_provider"] == "shared_llm_client_opt_in"
    assert llm_boundaries["telegram_chart_formatter_uses_llm_provider_env"] is True
    assert llm_boundaries["telegram_chart_formatter_launcher_sets_shared_router"] is True
    assert llm_boundaries["telegram_chart_formatter_effective_shared_router"] is True
    assert llm_boundaries["telegram_chart_formatter_effective_provider"] == "alibaba"
    assert llm_boundaries["telegram_chart_formatter_effective_provider_scope"] == "shared_llm_client_opt_in"
    assert llm_boundaries["telegram_chart_formatter_effective_shared_entrypoints"] == [
        "generate_client_text",
        "generate_edu_text",
    ]
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


def test_operational_health_reports_main_instruction_skip_reasons(tmp_path, monkeypatch):
    view = tmp_path / "state" / "derived" / "main_paper_instructions.json"
    view.parent.mkdir(parents=True)
    view.write_text(
        json.dumps({
            "instructions": 0,
            "active_source_signals": 2,
            "skipped_unvalidated": 2,
            "skip_reasons": {"missing_ready_strategy_id": 2},
            "skipped_examples": [
                {
                    "signal_id": "sig_1",
                    "symbol": "BICO_USDT_SWAP",
                    "timeframe": "1h",
                    "family": "early_tp_tactical",
                    "reason": "missing_ready_strategy_id",
                }
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    instructions = report["paper_chain"]["instructions"]
    assert instructions["instructions"] == 0
    assert instructions["skip_reasons"] == {"missing_ready_strategy_id": 2}
    assert instructions["skipped_examples"][0]["symbol"] == "BICO_USDT_SWAP"


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
    assert report["readiness"]["paper_runtime_observed"]["status"] == "warn"
    assert report["readiness"]["paper_main_runtime_current"]["status"] == "warn"
    assert report["readiness"]["main_runtime_consumer"]["status"] == "planned"
    assert report["paper_data_flow"]["current_main_compatible_runtime"] == "src.research_lab.main_paper_runtime"


def test_operational_health_reports_paper_telegram_preview(tmp_path, monkeypatch):
    preview = tmp_path / "state" / "derived" / "paper_telegram_preview.json"
    preview.parent.mkdir(parents=True)
    preview.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["journals"]["paper_telegram_preview_snapshot"]["exists"] is True
    assert report["readiness"]["paper_telegram_preview_available"]["status"] == "pass"


def test_operational_health_reports_paper_telegram_sender(tmp_path, monkeypatch):
    preview = tmp_path / "state" / "derived" / "paper_telegram_preview.json"
    delivery = tmp_path / "state" / "derived" / "paper_telegram_delivery.json"
    delivery.parent.mkdir(parents=True)
    preview.write_text(
        json.dumps({"records_read": 2, "rendered": 2, "invalid": 0, "items": [{}, {}]}),
        encoding="utf-8",
    )
    delivery.write_text(
        json.dumps({
            "records_read": 2,
            "eligible": 2,
            "sent": 0,
            "skipped": 2,
            "errors": 0,
            "items": [
                {"status": "dry_run", "problem": ""},
                {"status": "skipped_no_paper_chat", "problem": "paper_telegram_not_configured"},
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["journals"]["paper_telegram_delivery_snapshot"]["exists"] is True
    assert report["paper_chain"]["telegram_delivery"]["eligible"] == 2
    assert report["paper_chain"]["telegram_delivery"]["skipped"] == 2
    breakdown = report["paper_chain"]["telegram_delivery_breakdown"]
    assert breakdown["by_status"] == {"dry_run": 1, "skipped_no_paper_chat": 1}
    assert breakdown["by_problem"] == {"paper_telegram_not_configured": 1}
    assert report["paper_chain"]["telegram_delivery_freshness"]["stale_vs_source"] is False
    assert report["readiness"]["paper_telegram_sender_available"]["status"] == "pass"


def test_operational_health_warns_on_stale_paper_telegram_delivery(tmp_path, monkeypatch):
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    preview = derived / "paper_telegram_preview.json"
    delivery = derived / "paper_telegram_delivery.json"
    preview.write_text(
        json.dumps({"records_read": 2, "rendered": 2, "invalid": 0, "items": [{}, {}]}),
        encoding="utf-8",
    )
    delivery.write_text(
        json.dumps({"records_read": 2, "eligible": 2, "sent": 0, "skipped": 2, "errors": 0, "items": []}),
        encoding="utf-8",
    )
    os.utime(delivery, (1000, 1000))
    os.utime(preview, (1005, 1005))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    freshness = report["paper_chain"]["telegram_delivery_freshness"]
    assert freshness["source_exists"] is True
    assert freshness["derived_exists"] is True
    assert freshness["stale_vs_source"] is True
    assert report["readiness"]["paper_telegram_sender_available"]["status"] == "warn"
    assert "paper_telegram_sender" in report["readiness"]["paper_telegram_sender_available"]["action"]


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
    os.utime(derived / "paper_signal_training.jsonl", (1000, 1000))
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
    assert report["readiness"]["paper_chain_counts"]["status"] == "warn"
    assert report["readiness"]["paper_runtime_observed"]["status"] == "warn"
    assert report["readiness"]["paper_main_runtime_current"]["status"] == "warn"
    assert report["readiness"]["training_data_exports"]["status"] == "pass"
    assert report["readiness"]["paper_signal_training_export"]["status"] == "pass"
    assert report["readiness"]["ready_for_visible_paper_research_loop"]["status"] == "warn"


def test_operational_health_treats_empty_pfr_trigger_cycle_as_ready_idle(tmp_path, monkeypatch):
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
        json.dumps({"instructions": 0, "skip_reasons": {"missing_ready_strategy_id": 10}, "items": []}),
        encoding="utf-8",
    )
    (tmp_path / "state" / "farm_loop_status.json").write_text(
        json.dumps({
            "schema": "FarmLoopStatus.v1",
            "pid": 0,
            "stage": "cycle_complete",
            "paper_only": True,
            "execution_allowed": False,
            "details": {
                "last_summary": {
                    "pfr_counts": {
                        "pfr_unique_setups": 11,
                        "pfr_rejected:no_breakout": 6,
                        "pfr_rejected:no_fade_signal:move_pct_threshold=8.0": 5,
                    },
                    "main_paper_bridge": {"instructions": 0},
                }
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=pfr)

    gate = report["readiness"]["ready_for_visible_paper_research_loop"]
    assert gate["status"] == "pass"
    assert "no live entry trigger" in gate["message"]


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
    assert H.has_blocked_readiness(report) is True
    assert H.exit_code_for_report(report, fail_on_blocked=True) == 2
    assert H.exit_code_for_report(report, fail_on_blocked=False) == 0


def test_operational_health_fail_on_blocked_ignores_warn_and_planned(tmp_path, monkeypatch):
    monkeypatch.delenv("STRATEGY_LAB_LLM_ENABLED", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["readiness"]["main_runtime_consumer"]["status"] == "planned"
    assert any(gate["status"] == "warn" for gate in report["readiness"].values())
    assert H.has_blocked_readiness(report) is False
    assert H.exit_code_for_report(report, fail_on_blocked=True) == 0

    next_actions = report["operator_next_actions"]
    assert next_actions["schema"] == "operator_next_actions.v1"
    assert next_actions["launch_blocked"] is False
    assert next_actions["blocking"] == []
    assert next_actions["status_counts"]["planned"] >= 1
    assert next_actions["status_counts"]["warn"] >= 1
    assert {item["name"] for item in next_actions["intentional_boundaries"]} == {
        "main_runtime_consumer",
        "manual_product_analyzer_boundary",
    }
    assert "paper_telegram_surface" in {
        item["name"] for item in next_actions["operator_configuration"]
    }
    assert "paper_chain_counts" in {item["name"] for item in next_actions["rebuild_actions"]}


def test_operational_health_operator_next_actions_exposes_blockers(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LLM_ENABLED", "1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_PROVIDER", "alibaba")
    monkeypatch.setenv("STRATEGY_LAB_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("STRATEGY_LAB_LLM_MODEL_CHEAP", "qwen-test")
    monkeypatch.delenv("STRATEGY_LAB_LLM_API_KEY", raising=False)
    monkeypatch.delenv("ALIBABA_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")
    next_actions = report["operator_next_actions"]

    assert next_actions["launch_blocked"] is True
    assert "strategy_lab_llm_policy" in {item["name"] for item in next_actions["blocking"]}
    assert "strategy_lab_llm_policy" in {
        item["name"] for item in next_actions["operator_configuration"]
    }
