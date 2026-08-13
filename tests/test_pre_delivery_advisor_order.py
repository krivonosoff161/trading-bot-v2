from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.strategy_lab import farm_loop
from src.research_lab import outcome_retest_result, paper_lineage, paper_telegram_preview
from src.research_lab.paper_signals import training_export
from src.research_lab.validation_generation import CurrentGenerationSnapshot


RUN_ID = "paperrun_1"
FEATURE_ONE = "fp_1111111111111111"
FEATURE_TWO = "fp_2222222222222222"


def _queue_snapshot(tmp_path: Path, items: object) -> Path:
    snapshot = tmp_path / "state" / "derived" / "main_paper_runtime_queue.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(
        json.dumps(
            {
                "schema": "main_paper_runtime_adapter.v1",
                "paper_generation_run_id": RUN_ID,
                "items": items,
            }
        ),
        encoding="utf-8",
    )
    return snapshot


def _queue_item(feature_id: str, *, tier: str = "validated_pfr") -> dict:
    return {
        "schema": "MainPaperRuntimeQueueItem.v1",
        "paper_generation_run_id": RUN_ID,
        "validation_tier": tier,
        "feature_packet_id": feature_id,
    }


def test_generation_feature_ids_are_exact_run_bound_and_validated(tmp_path) -> None:
    snapshot = _queue_snapshot(
        tmp_path,
        [
            _queue_item(FEATURE_ONE),
            _queue_item("", tier="farm_calculated"),
            _queue_item(FEATURE_ONE),
            _queue_item(FEATURE_TWO),
        ],
    )

    assert farm_loop._generation_feature_packet_ids(
        {
            "paper_generation_run_id": RUN_ID,
            "snapshot_path": str(snapshot),
        },
        expected_run_id=RUN_ID,
        private_root=tmp_path,
    ) == [FEATURE_ONE, FEATURE_TWO]


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": "wrong", "paper_generation_run_id": RUN_ID, "items": []},
        {
            "schema": "main_paper_runtime_adapter.v1",
            "paper_generation_run_id": RUN_ID,
            "items": {},
        },
        {
            "schema": "main_paper_runtime_adapter.v1",
            "paper_generation_run_id": RUN_ID,
            "items": [_queue_item("../escape")],
        },
    ],
)
def test_generation_feature_ids_fail_closed_on_schema_list_and_id(
    tmp_path, payload
) -> None:
    snapshot = _queue_snapshot(tmp_path, [])
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError):
        farm_loop._generation_feature_packet_ids(
            {"paper_generation_run_id": RUN_ID, "snapshot_path": str(snapshot)},
            expected_run_id=RUN_ID,
            private_root=tmp_path,
        )


@pytest.mark.parametrize("kind", ["relative", "absolute_outside", "parent_escape"])
def test_generation_feature_ids_reject_noncanonical_snapshot_paths(tmp_path, kind) -> None:
    _queue_snapshot(tmp_path, [])
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    if kind == "relative":
        supplied = Path("state/derived/main_paper_runtime_queue.json")
    elif kind == "parent_escape":
        supplied = tmp_path / "state" / "derived" / ".." / ".." / "outside.json"
    else:
        supplied = outside

    with pytest.raises(RuntimeError, match="canonical|escapes"):
        farm_loop._generation_feature_packet_ids(
            {"paper_generation_run_id": RUN_ID, "snapshot_path": str(supplied)},
            expected_run_id=RUN_ID,
            private_root=tmp_path,
        )


def test_generation_feature_ids_reject_symlink_or_reparse_escape(tmp_path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    expected = tmp_path / "state" / "derived" / "main_paper_runtime_queue.json"
    expected.parent.mkdir(parents=True)
    try:
        expected.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {type(exc).__name__}")

    with pytest.raises(RuntimeError, match="escapes"):
        farm_loop._generation_feature_packet_ids(
            {"paper_generation_run_id": RUN_ID, "snapshot_path": str(expected)},
            expected_run_id=RUN_ID,
            private_root=tmp_path,
        )


def test_generation_feature_ids_reject_reparse_flag_even_without_symlink_privilege(
    tmp_path, monkeypatch
) -> None:
    snapshot = _queue_snapshot(tmp_path, [])
    monkeypatch.setattr(farm_loop, "is_link_or_reparse", lambda path: path == snapshot)

    with pytest.raises(RuntimeError, match="escapes"):
        farm_loop._generation_feature_packet_ids(
            {"paper_generation_run_id": RUN_ID, "snapshot_path": str(snapshot)},
            expected_run_id=RUN_ID,
            private_root=tmp_path,
        )


def test_feature_packet_path_rejects_id_and_symlink_escape(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="identity"):
        farm_loop._canonical_feature_packet_path(tmp_path, "../escape")

    outside = tmp_path / "outside-feature.json"
    outside.write_text("{}", encoding="utf-8")
    candidate = tmp_path / "features" / "decision" / f"{FEATURE_ONE}.json"
    candidate.parent.mkdir(parents=True)
    try:
        candidate.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {type(exc).__name__}")
    with pytest.raises(RuntimeError, match="escapes"):
        farm_loop._canonical_feature_packet_path(tmp_path, FEATURE_ONE)


@pytest.mark.parametrize(
    "packet",
    [
        {"schema": "wrong", "feature_packet_id": FEATURE_ONE},
        {
            "schema": "DecisionFeaturePacket.v1",
            "feature_packet_id": FEATURE_TWO,
            "scanner_event_id": "scan_1",
            "data_packet_id": "data_1",
            "symbol": "BTC_USDT_SWAP",
            "instrument": "BTC-USDT-SWAP",
            "timeframe": "1h",
            "mode": "live",
            "features": {},
            "geometry": {},
            "data_quality": {},
            "no_lookahead": True,
        },
    ],
)
def test_pre_delivery_advisor_rejects_feature_schema_or_content_identity(
    tmp_path, monkeypatch, packet
) -> None:
    path = tmp_path / "features" / "decision" / f"{FEATURE_ONE}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(packet), encoding="utf-8")
    monkeypatch.setattr(
        "src.research_lab.llm_provider.load_provider", lambda _env: object()
    )

    with pytest.raises((RuntimeError, ValueError)):
        farm_loop._run_calculator_advisor_stage(
            SimpleNamespace(
                calculator_advisor_max_calls=1,
                calculator_provider="",
                calculator_model="",
                calculator_base_url="",
                calculator_timeout=0,
                allow_public_output=False,
            ),
            tmp_path,
            True,
            feature_packet_ids=[FEATURE_ONE],
        )


def test_v2_advisor_runs_before_preview_and_falls_back_without_blocking(
    tmp_path, monkeypatch
) -> None:
    order: list[str] = []
    queue_snapshot = _queue_snapshot(
        tmp_path,
        [_queue_item(FEATURE_ONE)],
    )

    class Runtime:
        database_path = tmp_path / "paper.sqlite3"

        def raise_if_failed(self) -> None:
            return None

        def run(self, **_kwargs):
            return {
                "run_id": RUN_ID,
                "producer_generation_id": "producer_1",
                "account_generation_id": "account_1",
                "bridge": {},
                "consumer": {},
                "queue": {
                    "paper_generation_run_id": RUN_ID,
                    "snapshot_path": str(queue_snapshot),
                },
                "observer": {},
                "trades": {},
                "producer_membership": {
                    "active_executable_signals": 1,
                    "validation_bound_members": 1,
                    "research_only_excluded": 0,
                    "authority_source": "pfr_farm",
                },
            }

    monkeypatch.setattr(
        "src.research_lab.validation_generation.load_current_generation_snapshot",
        lambda _root: CurrentGenerationSnapshot("ready", "hvg_1", {}, 1),
    )

    def advisor(*_args, **_kwargs):
        order.append("advisor")
        raise TimeoutError("synthetic")

    def preview(*_args, **_kwargs):
        order.append("preview")
        return {
            "current_generation_compatible": True,
            "paper_generation_run_id": RUN_ID,
            "analysis_fallback": 1,
        }

    monkeypatch.setattr(farm_loop, "_run_calculator_advisor_stage", advisor)
    monkeypatch.setattr(paper_telegram_preview, "build_paper_telegram_preview", preview)
    monkeypatch.setattr(
        training_export,
        "export_training_rows",
        lambda *_a, **_k: {
            "current_generation_compatible": True,
            "paper_generation_run_id": RUN_ID,
        },
    )
    monkeypatch.setattr(
        paper_lineage,
        "build_paper_lineage",
        lambda *_a, **_k: {
            "current_generation_compatible": True,
            "paper_generation_run_id": RUN_ID,
        },
    )
    monkeypatch.setattr(
        outcome_retest_result,
        "build_outcome_retest_results",
        lambda *_a, **_k: {
            "training_evidence": {
                "current_generation_compatible": True,
                "paper_generation_run_id": RUN_ID,
            }
        },
    )

    out: dict = {}
    farm_loop._run_v2_main_paper_derived_chain(
        SimpleNamespace(
            paper_generation_runtime=Runtime(),
            run_calculator_advisor=True,
        ),
        tmp_path,
        tasks=object(),
        apply=True,
        loop=True,
        cycle_started_at=1.0,
        out=out,
        provider=object(),
    )

    assert order == ["advisor", "preview"]
    assert out["calculator_advisor"]["requested"] == 1
    assert out["calculator_advisor"]["eligible"] == 1
    assert out["calculator_advisor"]["attempted"] == 0
    assert out["calculator_advisor"]["accepted"] == 0
    assert out["calculator_advisor"]["fallback"] == 1
    assert out["calculator_advisor"]["blocked"] == 1
    assert out["calculator_advisor"]["pre_delivery"] is True
    assert out["paper_telegram_preview"]["analysis_fallback"] == 1


def test_advisor_metrics_count_existing_advice_without_duplicate_attempt(tmp_path) -> None:
    advice_path = tmp_path / "state" / "llm_advice" / "calculator_advice.jsonl"
    advice_path.parent.mkdir(parents=True)
    advice_path.write_text(
        json.dumps(
            {
                "schema": "CalculatorAdvice.v1",
                "advisor_ref": "advice_existing",
                "feature_packet_id": FEATURE_ONE,
                "accepted": True,
                "advice": {
                    "situation_class": "trend",
                    "user_facing_analysis": (
                        "Trend context remains uncertain and requires deterministic "
                        "confirmation."
                    ),
                },
                "paper_only": True,
                "execution_allowed": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = farm_loop._run_calculator_advisor_stage(
        SimpleNamespace(calculator_advisor_max_calls=1),
        tmp_path,
        True,
        feature_packet_ids=[FEATURE_ONE],
    )

    assert result["requested"] == 1
    assert result["eligible"] == 1
    assert result["attempted"] == 0
    assert result["accepted"] == 1
    assert result["fallback"] == 0
    assert result["reason_counts"] == {"advice_already_available": 1}
