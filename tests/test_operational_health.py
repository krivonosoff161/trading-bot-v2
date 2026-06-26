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
    assert "secret-token" not in rendered
    assert "secret-alibaba" not in rendered


def test_operational_health_reports_existing_journal_files(tmp_path, monkeypatch):
    pfr = tmp_path / "state" / "strategy_lab.sqlite"
    pfr.parent.mkdir(parents=True)
    pfr.write_bytes(b"sqlite")
    paper = tmp_path / "state" / "derived" / "paper_signals.jsonl"
    paper.parent.mkdir(parents=True)
    paper.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    report = H.collect(private_root=tmp_path, pfr_db_path=pfr)

    assert report["pfr"]["db"]["exists"] is True
    assert report["journals"]["paper_signals"]["exists"] is True
    assert Path(report["pfr"]["db"]["path"]) == pfr
