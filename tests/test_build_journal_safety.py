import asyncio
import json

import scripts.build_journal as journal


def test_fetch_positions_is_private_opt_in(monkeypatch):
    monkeypatch.delenv("JOURNAL_ENABLE_PRIVATE_FILLS", raising=False)
    monkeypatch.setenv("OKX_API_KEY", "present-but-must-not-be-used")

    def forbidden_client(*args, **kwargs):
        raise AssertionError("private OKX client must be opt-in for journal builds")

    monkeypatch.setattr(journal, "_create_okx_client", forbidden_client)

    assert asyncio.run(journal._fetch_positions_async()) == []


def test_paper_watch_loader_reads_private_derived_rows_only(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", str(tmp_path))
    derived = tmp_path / "state" / "derived"
    derived.mkdir(parents=True)
    rows_path = derived / "paper_signal_training.jsonl"
    rows_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "schema": "PaperSignalTrainingRow.v1",
                        "signal_id": "sig-1",
                        "created_at": "2026-06-26T00:00:00Z",
                        "symbol": "BICO_USDT_SWAP",
                        "okx_inst_id": "BICO-USDT-SWAP",
                        "timeframe": "15m",
                        "family": "early_tp_tactical",
                        "side": "short",
                        "exit_mode": "partial_be",
                        "status": "reviewed",
                        "result": "stop",
                        "net_pct": -1.2,
                        "net_r": -1.0,
                        "mfe_pct": 0.4,
                        "mae_pct": 1.5,
                        "capture": -0.1,
                        "diagnosis": "wrong_direction",
                        "risk_pct": 2.5,
                        "max_hold_minutes": 180,
                        "source": "paper_signals",
                        "paper_only": True,
                    },
                    sort_keys=True,
                ),
                json.dumps({"schema": "Unknown.v1", "symbol": "SHOULD_SKIP"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = journal._load_paper_watch()

    assert len(rows) == 1
    assert rows[0]["okx_inst_id"] == "BICO-USDT-SWAP"
    assert rows[0]["paper_only"] is True
    assert rows[0]["diagnosis"] == "wrong_direction"


def test_paper_watch_sheet_is_created(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_BOT_RESEARCH_ROOT", str(tmp_path))
    rows = [
        {
            "created_at": "2026-06-26T00:00:00Z",
            "symbol": "BICO_USDT_SWAP",
            "okx_inst_id": "BICO-USDT-SWAP",
            "timeframe": "15m",
            "family": "early_tp_tactical",
            "side": "short",
            "exit_mode": "partial_be",
            "status": "reviewed",
            "result": "take",
            "net_pct": 1.2,
            "net_r": 0.8,
            "mfe_pct": 1.8,
            "mae_pct": 0.2,
            "capture": 0.66,
            "diagnosis": "good_signal",
            "risk_pct": 2.5,
            "max_hold_minutes": 180,
            "source": "paper_signals",
            "paper_only": True,
        }
    ]
    wb = journal.openpyxl.Workbook()

    journal._build_paper_watch(wb, rows)

    ws = wb["Paper Watch"]
    assert ws["A1"].value == "Created"
    assert ws["C2"].value == "BICO-USDT-SWAP"
    assert ws["J2"].value == 1.2
    assert ws["D4"].value == "Family"
