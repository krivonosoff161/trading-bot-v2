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
    assert report["readiness"]["main_paper_consumer_available"]["status"] == "warn"
    assert report["readiness"]["main_paper_runtime_queue_available"]["status"] == "warn"
    assert report["readiness"]["main_runtime_consumer"]["status"] == "planned"
    assert report["readiness"]["paper_telegram_preview_available"]["status"] == "warn"
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
    assert report["readiness"]["paper_telegram_preview_available"]["status"] == "warn"
    assert report["readiness"]["training_data_exports"]["status"] == "pass"
    assert Path(report["pfr"]["db"]["path"]) == pfr


def test_operational_health_reports_main_instruction_view(tmp_path, monkeypatch):
    view = tmp_path / "state" / "derived" / "main_paper_instructions.json"
    view.parent.mkdir(parents=True)
    view.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["main_bridge"]["instruction_view_exists"] is True
    assert report["main_bridge"]["status"] == "instruction_view_ready_not_consumed"
    assert report["main_bridge"]["orders_enabled_by_bridge"] is False
    assert report["readiness"]["main_instruction_view_available"]["status"] == "pass"


def test_operational_health_reports_main_paper_consumer_view(tmp_path, monkeypatch):
    view = tmp_path / "state" / "derived" / "main_paper_consumed.json"
    view.parent.mkdir(parents=True)
    view.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["main_bridge"]["consumer_view_exists"] is True
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
    assert report["readiness"]["main_paper_runtime_queue_available"]["status"] == "pass"
    assert report["readiness"]["main_runtime_consumer"]["status"] == "planned"


def test_operational_health_reports_paper_telegram_preview(tmp_path, monkeypatch):
    preview = tmp_path / "state" / "derived" / "paper_telegram_preview.json"
    preview.parent.mkdir(parents=True)
    preview.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    report = H.collect(private_root=tmp_path, pfr_db_path=tmp_path / "missing.sqlite")

    assert report["journals"]["paper_telegram_preview_snapshot"]["exists"] is True
    assert report["readiness"]["paper_telegram_preview_available"]["status"] == "pass"


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
