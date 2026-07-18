"""Explicit end-to-end orchestration for ``PaperGenerationRun.v2``.

The caller supplies the already activated evidence store, its co-located writer
lease, an immutable account generation, and a bounded public/synthetic candle
provider.  Nothing in this module starts processes, discovers credentials, chooses a
live provider, sends messages, or enables execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.research_lab.main_paper_bridge import (
    ACTIVE_STATUSES,
    export_main_paper_instructions,
    instruction_from_signal,
)
from src.research_lab.main_paper_consumer import consume_main_paper_instructions
from src.research_lab.main_paper_runtime import CandleProvider, observe_main_paper_runtime
from src.research_lab.main_paper_runtime_adapter import build_main_paper_runtime_queue
from src.research_lab.main_paper_trade_ledger import build_main_paper_trade_ledger
from src.research_lab.paper_evidence_store import (
    PaperEvidenceConflict,
    PaperEvidenceStore,
    PaperWriterLease,
)
from src.research_lab.paper_generation_contract import (
    PaperGenerationContext,
    canonical_digest,
)
from src.research_lab.paper_signals.store import load_signals_strict


def _content_only_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    generation_fields = {
        "paper_generation_run_id",
        "source_producer_generation_id",
        "source_member_payload_digest",
        "source_validation_generation_id",
        "consumer_output_digest",
    }
    return {key: value for key, value in item.items() if key not in generation_fields}


def _scenario_candidates(queue_items: list[dict[str, Any]]) -> dict[str, tuple[str, list[str]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in queue_items:
        key = canonical_digest(
            {
                "instrument": item.get("okx_inst_id"),
                "timeframe": item.get("timeframe"),
                "side": item.get("side"),
                "boundary_ts": item.get("boundary_ts"),
            }
        )
        grouped.setdefault(key, []).append(item)
    result: dict[str, tuple[str, list[str]]] = {}
    for candidates in grouped.values():
        ordered = sorted(
            candidates,
            key=lambda row: (
                int(row.get("priority") or 0),
                str(row.get("source_signal_id") or ""),
            ),
        )
        identities = [
            f"{index:06d}:{row.get('source_signal_id') or ''}"
            for index, row in enumerate(ordered)
        ]
        for identity, row in zip(identities, ordered, strict=True):
            result[str(row.get("source_signal_id") or "")] = (identity, identities)
    return result


def _plan_observed_transition(
    store: PaperEvidenceStore,
    lease: PaperWriterLease,
    *,
    run_id: str,
    subject_generation_id: str,
    observation_id: str,
    observed: dict[str, Any],
    account_generation_id: str,
    scenario_id: str,
    scenario_candidates: list[str],
    allow_new_terminal_close: bool = True,
) -> list[str]:
    state = store.replay_lifecycle(subject_generation_id)["state"]
    signal_status = str(observed.get("signal_status") or "")
    outcome = dict(observed.get("outcome") or {})
    has_open = bool(
        outcome.get("opened_at_bar_ts")
        or signal_status == "opened_paper"
        or outcome.get("net_pct") not in (None, "")
    )
    is_terminal = bool(
        signal_status in {"closed_paper", "expired", "reviewed", "invalidated"}
        or outcome.get("net_pct") not in (None, "")
    )
    intents: list[str] = []
    if has_open and state == "armed":
        intents.append(
            store.plan_lifecycle(
                lease,
                run_id=run_id,
                subject_generation_id=subject_generation_id,
                observation_id=observation_id,
                event_type="position_opened",
                payload={
                    "scenario_id": scenario_id,
                    "scenario_candidates": scenario_candidates,
                },
                account_generation_id=account_generation_id,
            )
        )
        state = "opened"
    if not is_terminal or outcome.get("net_pct") in (None, ""):
        return intents
    net_pct = float(outcome["net_pct"])
    if state == "opened" and (allow_new_terminal_close or len(intents) == 0):
        intents.append(
            store.plan_lifecycle(
                lease,
                run_id=run_id,
                subject_generation_id=subject_generation_id,
                observation_id=observation_id,
                event_type="position_closed",
                payload={"net_pct": net_pct},
                account_generation_id=account_generation_id,
            )
        )
        return intents
    if state in {"closed", "revised"}:
        prior = store.latest_terminal_event(subject_generation_id)
        if prior is None:
            raise PaperEvidenceConflict("terminal lifecycle cursor lacks terminal event")
        prior_payload = json.loads(str(prior["payload_json"]))
        if float(prior_payload.get("net_pct")) == net_pct:
            return intents
        intents.append(
            store.plan_lifecycle(
                lease,
                run_id=run_id,
                subject_generation_id=subject_generation_id,
                observation_id=observation_id,
                event_type="outcome_revised",
                payload={"net_pct": net_pct},
                account_generation_id=account_generation_id,
                supersedes_event_id=str(prior["lifecycle_event_id"]),
            )
        )
    return intents


def run_paper_generation_v2(
    private_root: Path,
    *,
    store: PaperEvidenceStore,
    lease: PaperWriterLease,
    account_generation_id: str,
    provider: CandleProvider,
    producer_id: str,
    producer_sequence: int,
    code_identity: str,
    producer_method_identity: str,
    simulator_manifest_id: str,
    lifecycle_method_identity: str,
    now_ms: int,
    parent_producer_generation_id: str | None = None,
) -> dict[str, Any]:
    """Build and atomically promote one fully verified paper generation."""
    if provider is None:
        raise ValueError("v2 paper generation requires an explicit bounded provider")
    if not simulator_manifest_id or not lifecycle_method_identity:
        raise ValueError("simulator and lifecycle method identities are required")
    signals = load_signals_strict(Path(private_root))
    selected = [
        signal
        for signal in signals
        if signal.status in ACTIVE_STATUSES and instruction_from_signal(signal) is not None
    ]
    members: list[dict[str, str]] = []
    for signal in selected:
        validation_generation_id = str(
            signal.validation_id or signal.validator_context.get("validation_id") or ""
        )
        if not validation_generation_id:
            raise PaperEvidenceConflict(
                f"producer member {signal.signal_id} lacks validation generation identity"
            )
        members.append(
            {
                "logical_id": signal.signal_id,
                "payload_digest": canonical_digest(signal.to_dict()),
                "source_validation_generation_id": validation_generation_id,
                "disposition": "active",
            }
        )
    producer_generation_id = store.register_producer_generation(
        lease,
        producer_id=producer_id,
        producer_sequence=producer_sequence,
        members=members,
        code_identity=code_identity,
        method_identity=producer_method_identity,
        parent_generation_id=parent_producer_generation_id,
    )
    run_id = store.create_run(lease, producer_generation_id=producer_generation_id)
    active_stage = "bridge"
    try:
        bridge = export_main_paper_instructions(
            private_root,
            generation_context=PaperGenerationContext(
                run_id,
                producer_generation_id,
                producer_generation_id,
            ),
        )
        store.complete_stage(
            lease,
            run_id,
            "bridge",
            input_digest=producer_generation_id,
            output_digest=str(bridge["stage_output_digest"]),
        )

        active_stage = "consumer"
        consumer = consume_main_paper_instructions(
            private_root,
            expected_run_id=run_id,
            expected_input_digest=producer_generation_id,
        )
        store.complete_stage(
            lease,
            run_id,
            "consumer",
            input_digest=str(bridge["stage_output_digest"]),
            output_digest=str(consumer["stage_output_digest"]),
        )

        active_stage = "queue"
        queue = build_main_paper_runtime_queue(
            private_root,
            limit=-1,
            expected_run_id=run_id,
            expected_input_digest=str(bridge["stage_output_digest"]),
        )
        store.complete_stage(
            lease,
            run_id,
            "queue",
            input_digest=str(consumer["stage_output_digest"]),
            output_digest=str(queue["stage_output_digest"]),
        )
        queue_payload = json.loads(Path(queue["snapshot_path"]).read_text(encoding="utf-8"))
        queue_items = list(queue_payload.get("items") or [])
        subjects: dict[str, str] = {}
        account_state = store.replay_account(account_generation_id)
        for item in queue_items:
            logical_id = str(item.get("source_signal_id") or "")
            subject_payload = {
                "queue_payload_digest": canonical_digest(_content_only_queue_item(item)),
                "source_member_payload_digest": str(
                    item.get("source_member_payload_digest") or ""
                ),
                "source_validation_generation_id": str(
                    item.get("source_validation_generation_id") or ""
                ),
                "simulator_manifest_id": simulator_manifest_id,
                "method_identity": lifecycle_method_identity,
                "paper_only": True,
                "execution_allowed": False,
            }
            active = store.active_subject(logical_id)
            supersedes = None
            if active is not None and json.loads(str(active["payload_json"])) != subject_payload:
                if str(active["subject_generation_id"]) in account_state["active_subjects"]:
                    raise PaperEvidenceConflict(
                        "active-position subject change requires explicit carry-forward generation"
                    )
                supersedes = str(active["subject_generation_id"])
            elif active is None:
                latest = store.latest_subject(logical_id)
                if (
                    latest is not None
                    and latest["state"] == "withdrawn"
                    and json.loads(str(latest["payload_json"])) != subject_payload
                ):
                    supersedes = str(latest["subject_generation_id"])
            subjects[logical_id] = store.register_subject(
                lease,
                run_id=run_id,
                logical_id=logical_id,
                payload=subject_payload,
                supersedes_generation_id=supersedes,
            )

        active_stage = "observer"

        def persist_observation(
            queue_item: dict[str, Any], manifest: dict[str, Any]
        ) -> dict[str, Any]:
            source_id = str(queue_item.get("source_signal_id") or "")
            subject_generation_id = subjects.get(source_id)
            if not subject_generation_id:
                raise PaperEvidenceConflict(
                    "observation source has no paper subject generation"
                )
            observation_id = store.record_observation(
                lease,
                run_id=run_id,
                subject_generation_id=subject_generation_id,
                rows=list(manifest["rows"]),
                request=dict(manifest["request"]),
                observed_at=float(manifest["observed_at_ms"]) / 1000.0,
                available_at=float(manifest["available_at_ms"]) / 1000.0,
                acquisition_id=str(manifest["acquisition_id"]),
                provider_identity=str(manifest["provider_identity"]),
                manifest_digest=str(manifest["manifest_digest"]),
            )
            persisted = store.observation(observation_id)
            return {
                **manifest,
                "observation_id": observation_id,
                "rows": persisted["rows"],
                "request": persisted["request"],
            }

        observer = observe_main_paper_runtime(
            private_root,
            limit=-1,
            apply=True,
            provider=provider,
            now_ms=now_ms,
            expected_run_id=run_id,
            expected_input_digest=str(consumer["stage_output_digest"]),
            persist_observation=persist_observation,
        )
        store.complete_stage(
            lease,
            run_id,
            "observer",
            input_digest=str(queue["stage_output_digest"]),
            output_digest=str(observer["stage_output_digest"]),
        )
        observations_by_source = {
            str(item.get("source_signal_id") or ""): item
            for item in observer.get("items") or []
        }
        scenarios = _scenario_candidates(queue_items)
        account_model = store.account_model(account_generation_id)
        predicted_available_units = int(account_state["available_margin_microunits"])
        position_margin_units = int(account_model["position_margin_microunits"])
        owned_scenario_sets = store.account_owned_scenario_sets(account_generation_id)
        terminal_refs_by_source: dict[str, str] = {}
        allocation_refs_by_source: dict[str, str] = {}
        account_decisions_by_source: dict[str, str] = {}
        for source_id, subject_generation_id in subjects.items():
            observed = observations_by_source.get(source_id) or {}
            manifest = observed.get("observation_manifest")
            if not isinstance(manifest, dict) or not manifest.get("rows"):
                continue
            observation_id = str(manifest.get("observation_id") or "")
            if not observation_id:
                raise PaperEvidenceConflict("observer omitted persisted observation identity")
            scenario_id, candidates = scenarios[source_id]
            lifecycle_state = store.replay_lifecycle(subject_generation_id)["state"]
            signal_status = str(observed.get("signal_status") or "")
            outcome = dict(observed.get("outcome") or {})
            has_open = bool(
                outcome.get("opened_at_bar_ts")
                or signal_status == "opened_paper"
                or outcome.get("net_pct") not in (None, "")
            )
            is_terminal = bool(
                signal_status in {"closed_paper", "expired", "reviewed", "invalidated"}
                or outcome.get("net_pct") not in (None, "")
            )
            scenario_set_digest = canonical_digest(sorted(set(candidates)))
            will_allocate = bool(
                lifecycle_state == "armed"
                and has_open
                and scenario_id == sorted(set(candidates))[0]
                and scenario_set_digest not in owned_scenario_sets
                and predicted_available_units >= position_margin_units
            )
            intent_ids = _plan_observed_transition(
                store,
                lease,
                run_id=run_id,
                subject_generation_id=subject_generation_id,
                observation_id=observation_id,
                observed=observed,
                account_generation_id=account_generation_id,
                scenario_id=scenario_id,
                scenario_candidates=candidates,
                allow_new_terminal_close=will_allocate,
            )
            for intent_id in intent_ids:
                intent_type = store.planned_lifecycle_event_type(intent_id)
                if intent_type == "position_opened":
                    allocation_refs_by_source[source_id] = (
                        store.planned_lifecycle_event_id(intent_id)
                    )
                elif intent_type in {"position_closed", "outcome_revised"}:
                    terminal_refs_by_source[source_id] = (
                        store.planned_lifecycle_event_id(intent_id)
                    )
            if lifecycle_state == "armed" and has_open:
                if will_allocate:
                    account_decisions_by_source[source_id] = "position_opened"
                elif (
                    scenario_id != sorted(set(candidates))[0]
                    or scenario_set_digest in owned_scenario_sets
                ):
                    account_decisions_by_source[source_id] = "counterfactual_excluded"
                else:
                    account_decisions_by_source[source_id] = "allocation_rejected"
            elif lifecycle_state == "opened" and is_terminal:
                account_decisions_by_source[source_id] = "position_closed"
            elif lifecycle_state in {"closed", "revised"} and is_terminal:
                account_decisions_by_source[source_id] = "terminal_unchanged"
                if source_id not in terminal_refs_by_source:
                    prior_terminal = store.latest_terminal_event(subject_generation_id)
                    if prior_terminal is not None:
                        terminal_refs_by_source[source_id] = str(
                            prior_terminal["lifecycle_event_id"]
                        )
                else:
                    account_decisions_by_source[source_id] = "pnl_adjustment"
            if lifecycle_state == "armed" and has_open and will_allocate:
                owned_scenario_sets.add(scenario_set_digest)
                if is_terminal and outcome.get("net_pct") not in (None, ""):
                    predicted_available_units += store.account_pnl_delta_microunits(
                        account_generation_id, outcome["net_pct"]
                    )
                else:
                    predicted_available_units -= position_margin_units
            elif (
                lifecycle_state == "opened"
                and is_terminal
                and outcome.get("net_pct") not in (None, "")
            ):
                predicted_available_units += position_margin_units
                predicted_available_units += store.account_pnl_delta_microunits(
                    account_generation_id, outcome["net_pct"]
                )
            elif (
                lifecycle_state in {"closed", "revised"}
                and is_terminal
                and outcome.get("net_pct") not in (None, "")
            ):
                prior_terminal = store.latest_terminal_event(subject_generation_id)
                if prior_terminal is not None:
                    prior_payload = json.loads(str(prior_terminal["payload_json"]))
                    predicted_available_units += store.account_pnl_delta_microunits(
                        account_generation_id,
                        float(outcome["net_pct"]) - float(prior_payload["net_pct"]),
                    )

        active_stage = "account"
        trades = build_main_paper_trade_ledger(
            private_root,
            expected_run_id=run_id,
            expected_input_digest=str(observer["stage_output_digest"]),
        )
        store.complete_stage(
            lease,
            run_id,
            "account",
            input_digest=str(observer["stage_output_digest"]),
            output_digest=str(trades["stage_output_digest"]),
        )
        projection_items = []
        for item in trades.get("items") or []:
            source_id = str(item.get("source_signal_id") or "")
            projection_items.append(
                {
                    **item,
                    "paper_subject_generation_id": subjects.get(source_id, ""),
                    "allocation_lifecycle_event_id": allocation_refs_by_source.get(
                        source_id, ""
                    ),
                    "terminal_lifecycle_event_id": terminal_refs_by_source.get(source_id, ""),
                    "paper_account_decision": account_decisions_by_source.get(source_id, ""),
                    "account_generation_id": account_generation_id,
                }
            )

        active_stage = "projection"
        pending_projection = store.prepare_projection(
            lease,
            run_id=run_id,
            projection_kind="trades",
            items=projection_items,
            input_projection_digests={"account": str(trades["stage_output_digest"])},
            target_path=Path(private_root) / "state" / "derived" / "main_paper_trades.v2.json",
            subject_generation_ids=list(subjects.values()),
            account_generation_id=account_generation_id,
        )
        projection_output_digest = canonical_digest(pending_projection)
        store.complete_stage(
            lease,
            run_id,
            "projection",
            input_digest=str(trades["stage_output_digest"]),
            output_digest=projection_output_digest,
        )
        finalized = store.finalize_run(lease, run_id)
    except Exception:
        # A post-commit verification error must remain visible, but the already
        # atomic/current run cannot be rewritten into a failed run.  Before
        # promotion, record the failure without permitting later stages to
        # continue or publish a mixed generation.
        if store.current_run_id() != run_id:
            try:
                store.fail_stage(
                    lease,
                    run_id,
                    active_stage,
                    reason=f"v2_generation_stage_failed:{active_stage}",
                )
            except PaperEvidenceConflict:
                store.abort_run(
                    lease,
                    run_id,
                    stage=active_stage,
                    reason=f"v2_generation_finalize_failed:{active_stage}",
                )
        raise
    return {
        "schema": "PaperGenerationRunResult.v2",
        "run_id": run_id,
        "producer_generation_id": producer_generation_id,
        "account_generation_id": account_generation_id,
        "bridge": bridge,
        "consumer": consumer,
        "queue": queue,
        "observer": observer,
        "trades": trades,
        "finalized": finalized,
        "paper_only": True,
        "execution_allowed": False,
    }
