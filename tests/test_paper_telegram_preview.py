import ast
import json
from pathlib import Path

from src.research_lab.paper_telegram_preview import (
    MAX_MESSAGE_CHARS,
    build_paper_telegram_preview,
    render_preview_text,
    validate_preview,
)


def _record(**overrides):
    row = {
        "consumer_id": "consumer_mainpaper_sig",
        "instruction_id": "mainpaper_sig",
        "source_signal_id": "sig",
        "pair": "BTC_USDT_SWAP",
        "okx_inst_id": "BTC-USDT-SWAP",
        "timeframe": "1h",
        "side": "long",
        "setup_family": "early_tp_tactical",
        "source_status": "armed",
        "consumer_status": "accepted_for_paper_watch",
        "problems": [],
        "paper_only": True,
        "execution_allowed": False,
        "signal_contract": {
            "pair": "BTC-USDT-SWAP",
            "side": "long",
            "entry": 100.5,
            "stop": 95.0,
            "max_hold_min": 600,
            "exit_rule": {
                "type": "scaled",
                "params": {
                    "targets": [
                        {"label": "tp1", "price": 110.0, "size_frac": 0.5},
                        {"label": "tp2", "price": 120.0, "size_frac": 0.5},
                    ]
                },
            },
            "follow": {"be_at_R": 1.0, "trail": {}},
            "regime": "paper_watch",
            "analyzer_id": "paper_signals.early_tp_tactical",
            "snapshot_id": "abc123",
            "ts": "2026-06-26T00:00:00+00:00",
            "metadata": {
                "reason_now": "safe <reason> & no order",
                "execution_allowed": False,
                "paper_only": True,
            },
        },
    }
    row.update(overrides)
    return row


def _write_consumer_snapshot(root: Path, rows: list[dict]) -> None:
    path = root / "state" / "derived" / "main_paper_consumed.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": "main_paper_consumer.v1",
                "instructions_read": len(rows),
                "accepted": sum(1 for row in rows if row.get("consumer_status") == "accepted_for_paper_watch"),
                "rejected": sum(1 for row in rows if row.get("consumer_status") != "accepted_for_paper_watch"),
                "items": rows,
            }
        ),
        encoding="utf-8",
    )


def test_preview_renders_safe_operator_card(tmp_path):
    _write_consumer_snapshot(tmp_path, [_record()])

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["records_read"] == 1
    assert summary["rendered"] == 1
    assert summary["invalid"] == 0
    assert summary["sends_network"] is False
    data = json.loads(Path(summary["snapshot_path"]).read_text(encoding="utf-8"))
    text = data["items"][0]["text"]
    assert "Paper-сетап:" in text
    assert "Идея:" in text
    assert "Вход:" in text
    assert "Источник:" in text
    assert "Это paper-наблюдение" in text
    assert "research-only, not an order" in text
    assert "execution_allowed=false" in text
    assert "&lt;reason&gt;" in text


def test_preview_skips_rejected_consumer_rows(tmp_path):
    _write_consumer_snapshot(
        tmp_path,
        [
            _record(consumer_status="rejected_contract", problems=["bad_schema"]),
            _record(instruction_id="mainpaper_ok"),
        ],
    )

    summary = build_paper_telegram_preview(tmp_path)

    assert summary["records_read"] == 2
    assert summary["rendered"] == 1
    assert summary["skipped_rejected"] == 1


def test_preview_validation_catches_bad_authority_and_length():
    row = _record(execution_allowed=True)
    text = render_preview_text(row) + ("x" * (MAX_MESSAGE_CHARS + 1))

    problems = validate_preview(row, text)

    assert "execution_allowed_not_false" in problems
    assert "telegram_message_too_long" in problems


def test_preview_writes_empty_snapshot_when_consumer_missing(tmp_path):
    summary = build_paper_telegram_preview(tmp_path)

    assert summary["source_exists"] is False
    assert summary["records_read"] == 0
    assert summary["rendered"] == 0
    assert Path(summary["snapshot_path"]).exists()


def test_paper_telegram_preview_has_no_sender_imports():
    path = Path("src/research_lab/paper_telegram_preview.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "src.utils.telegram",
        "aiohttp",
        "requests",
        "src.exchange",
        "scripts.auto_execute",
        "dotenv",
        "hmac",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & forbidden)
