import ast
import json
from pathlib import Path

from scripts.strategy_lab.agent_role_review_cycle import run_cycle
from src.research_lab.llm_provider import LLMUsage
from src.research_lab.llm_role_reviews import LOCAL_OUTCOME_REVIEW_PROMPT, request_role_review


class _Provider:
    name = "fake"
    configured = True

    def generate(self, system, user):
        data = json.loads(user)
        role = data["role_id"]
        if role == "outcome_reviewer":
            payload = {
                "summary": "Loss gave back after favourable move.",
                "review_kind": "loss",
                "outcome_bucket": "gave_back",
                "actionability": "retest_exit_or_capture",
                "diagnosis": "bad_exit_gave_back",
                "confidence": 0.7,
                "learning_tags": ["exit_capture"],
                "next_test_dimensions": ["partial_be"],
            }
        elif role == "validator_reviewer":
            payload = {
                "summary": "Too few trades for a hard verdict.",
                "validator_class": "underpowered",
                "failure_mode": "thin_sample",
                "confidence": 0.8,
            }
        elif role == "system_analyst":
            payload = {
                "summary": "Completed role work supports one bounded follow-up.",
                "next_test_dimensions": ["exit_policy"],
                "counterfactual_summary": "Compare the frozen exit policy.",
                "confidence": 0.8,
            }
        else:
            payload = {
                "summary": "Source needs later outcome confirmation.",
                "source_class": "unknown_trust",
                "trust_delta": 0,
                "confidence": 0.6,
            }
        return json.dumps(payload), LLMUsage(provider="fake", model="fake-json")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_agent_role_review_cycle_uses_real_private_artifacts(monkeypatch, tmp_path):
    _write_jsonl(
        tmp_path / "state" / "derived" / "paper_signal_training.jsonl",
        [
            {
                "training_row_id": "training_1",
                "symbol": "BTC_USDT_SWAP",
                "timeframe": "15m",
                "family": "early_tp_tactical",
                "diagnosis": "bad_exit_gave_back",
                "net_pct": -1.2,
                "mfe_pct": 2.0,
                "capture": 0.0,
                "paper_only": True,
                "execution_allowed": False,
            }
        ],
    )
    memory_path = tmp_path / "state" / "derived" / "setup_outcome_memory.json"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "symbol": "BTC_USDT_SWAP",
                        "timeframe": "15m",
                        "family": "momentum_breakout",
                        "outcome_class": "INSUFFICIENT_DATA",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        tmp_path / "state" / "lineage" / "scanner_events.jsonl",
        [
            {
                "scanner_event_id": "se_1",
                "symbol": "BTC_USDT_SWAP",
                "timeframe": "15m",
                "source": "farm",
                "reason": "test",
            }
        ],
    )

    monkeypatch.setattr("scripts.strategy_lab.agent_role_review_cycle._make_provider", lambda args: _Provider())
    args = type(
        "Args",
        (),
        {
            "private_root": tmp_path,
            "provider": "fake",
            "max_outcomes": 1,
            "max_validator": 1,
            "max_sources": 1,
            "sleep_seconds": 0,
        },
    )()
    summary = run_cycle(args)

    assert summary["reviews"] == 3
    assert summary["accepted"] == 3
    assert summary["outcome_learning"]["by_review_kind"]["loss"] == 1
    assert summary["outcome_learning"]["by_outcome_bucket"]["gave_back"] == 1
    assert summary["execution_allowed"] is False
    assert (tmp_path / "reports" / "agent_role_review_cycle" / "summary.json").exists()


def test_agent_role_review_cycle_prefers_unreviewed_training_rows(monkeypatch, tmp_path):
    _write_jsonl(
        tmp_path / "state" / "derived" / "paper_signal_training.jsonl",
        [
            {
                "training_row_id": "training_1",
                "symbol": "BTC_USDT_SWAP",
                "timeframe": "15m",
                "family": "early_tp_tactical",
                "diagnosis": "bad_exit_gave_back",
                "net_pct": -1.2,
                "paper_only": True,
                "execution_allowed": False,
            },
            {
                "training_row_id": "training_2",
                "symbol": "ETH_USDT_SWAP",
                "timeframe": "1h",
                "family": "momentum_breakout",
                "diagnosis": "good_signal",
                "net_pct": 1.2,
                "paper_only": True,
                "execution_allowed": False,
            },
            {
                "training_row_id": "training_3",
                "symbol": "SOL_USDT_SWAP",
                "timeframe": "4h",
                "family": "continuation",
                "diagnosis": "expired_no_entry",
                "net_pct": 0.0,
                "paper_only": True,
                "execution_allowed": False,
            },
        ],
    )
    _write_jsonl(
        tmp_path / "state" / "llm_advice" / "outcome_reviews.jsonl",
        [
            {
                "review_id": "llmr_existing",
                "role_id": "outcome_reviewer",
                "source_ref": "training_1",
                "accepted": True,
                "payload": {"summary": "already reviewed"},
                "paper_only": True,
                "execution_allowed": False,
            }
        ],
    )

    monkeypatch.setattr("scripts.strategy_lab.agent_role_review_cycle._make_provider", lambda args: _Provider())
    args = type(
        "Args",
        (),
        {
            "private_root": tmp_path,
            "provider": "fake",
            "max_outcomes": 2,
            "max_validator": 0,
            "max_sources": 0,
            "sleep_seconds": 0,
        },
    )()
    summary = run_cycle(args)

    assert summary["reviews"] == 2
    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "llm_advice" / "outcome_reviews.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    new_refs = [row["source_ref"] for row in rows if row.get("review_id") != "llmr_existing"]
    assert new_refs == ["training_2", "training_3"]


def test_agent_role_cycle_skips_exhausted_item_and_advances(monkeypatch, tmp_path):
    rows = []
    for index in range(4):
        rows.append({
            "training_row_id": f"training_{index}",
            "symbol": "BTC_USDT_SWAP",
            "timeframe": "15m",
            "family": "continuation",
            "diagnosis": "needs_review",
            "paper_only": True,
            "execution_allowed": False,
        })
    _write_jsonl(tmp_path / "state" / "derived" / "paper_signal_training.jsonl", rows)
    _write_jsonl(
        tmp_path / "state" / "llm_advice" / "outcome_reviews.jsonl",
        [
            {
                "review_id": f"failed_{index}",
                "role_id": "outcome_reviewer",
                "source_ref": "training_3",
                "accepted": False,
                "problems": ["schema mismatch"],
                "paper_only": True,
                "execution_allowed": False,
            }
            for index in range(3)
        ],
    )
    monkeypatch.setattr("scripts.strategy_lab.agent_role_review_cycle._make_provider", lambda args: _Provider())
    args = type("Args", (), {
        "private_root": tmp_path, "provider": "fake", "max_outcomes": 1,
        "max_validator": 0, "max_sources": 0, "sleep_seconds": 0,
    })()
    summary = run_cycle(args)
    assert summary["accepted"] == 1
    written = [
        json.loads(line) for line in
        (tmp_path / "state" / "llm_advice" / "outcome_reviews.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert written[-1]["source_ref"] == "training_2"
def test_agent_role_review_cycle_does_not_load_dotenv():
    source_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "strategy_lab"
        / "agent_role_review_cycle.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "dotenv" not in imported
    assert "load_dotenv" not in called


def test_agent_role_cycle_reviews_completed_role_result_once(monkeypatch, tmp_path):
    result = {
        "schema": "SystemAnalystResultInput.v1",
        "result_id": "role_result::env_1::farm",
        "environment_id": "env_1",
        "feedback_id": "feedback_1",
        "recipient": "farm",
        "result": {"status": "completed", "task_type": "run_sweep"},
        "task_spec": {"generation": 0, "paper_only": True},
        "paper_only": True,
        "execution_allowed": False,
    }
    _write_jsonl(
        tmp_path / "state" / "derived" / "system_analyst_result_inbox.jsonl",
        [result],
    )
    monkeypatch.setattr("scripts.strategy_lab.agent_role_review_cycle._make_provider", lambda args: _Provider())
    args = type("Args", (), {
        "private_root": tmp_path, "provider": "fake", "max_outcomes": 0,
        "max_validator": 0, "max_sources": 0, "max_analyst": 1,
        "sleep_seconds": 0,
    })()

    first = run_cycle(args)
    second = run_cycle(args)

    assert first["inputs"]["analyst_results"] == 1
    assert first["accepted"] == 1
    assert second["inputs"]["analyst_results"] == 0
    drafts = [
        json.loads(line)
        for line in (tmp_path / "state" / "llm_advice" / "system_analyst_drafts.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert drafts[-1]["source_ref"] == result["result_id"]


def test_local_outcome_review_uses_compact_contract(tmp_path):
    class LocalProvider(_Provider):
        name = "ollama"

        def generate(self, system, user):
            assert system == LOCAL_OUTCOME_REVIEW_PROMPT
            assert "counterfactual_tests" not in system
            return super().generate(system, user)

    review = request_role_review(
        tmp_path,
        role_id="outcome_reviewer",
        source_ref="training-local",
        source_payload={"schema": "OutcomeLearningCase.v1"},
        provider=LocalProvider(),
    )

    assert review.accepted is True
