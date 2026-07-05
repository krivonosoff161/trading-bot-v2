"""Run advisory LLM role reviews on real private Strategy Lab artifacts.

This is a bounded paper/research-only cycle. It reads recent private outcomes,
validator memory, and scanner events; asks the configured role provider for
advisory JSON; and writes the results back under the private research root.

It never reads .env directly, never sends Telegram, never changes trade levels,
and never enables execution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from src.research_lab.lineage_contract import utc_now  # noqa: E402
from src.research_lab.llm_provider import (  # noqa: E402
    DEFAULT_RATE_RUB_PER_1K,
    OllamaProposalProvider,
    OpenAICompatibleProvider,
    ProposalProvider,
)
from src.research_lab.llm_role_reviews import request_role_review, review_summary  # noqa: E402
from src.research_lab.outcome_learning import build_outcome_review_pack, learning_summary  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT, resolve_private_root  # noqa: E402


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, "") or default).strip()


def _make_provider(args: argparse.Namespace) -> ProposalProvider:
    provider = args.provider.lower()
    if provider == "ollama":
        return OllamaProposalProvider(
            base_url=args.base_url or "http://127.0.0.1:11434/v1",
            model=args.model or "calculator",
            timeout=args.timeout,
        )
    key = _env(args.api_key_env)
    base_url = args.base_url or _env("ALIBABA_BASE_URL")
    model = args.model or _env("LLM_CHEAP_MODEL") or _env("LLM_MODEL") or "qwen-plus"
    return OpenAICompatibleProvider(
        provider=provider,
        base_url=base_url,
        api_key=key,
        model=model,
        timeout=args.timeout,
        rate_rub_per_1k=args.rate_rub_per_1k,
    )


def _read_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-limit:]


def _load_validator_memory(private_root: Path, limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    path = private_root / "state" / "derived" / "setup_outcome_memory.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    rows = [row for row in (data.get("records") or data.get("items") or []) if isinstance(row, dict)]
    interesting = [
        row for row in rows
        if str(row.get("outcome_class") or row.get("lite_status") or row.get("validation_status") or "")
    ]
    return (interesting or rows)[-limit:]


def _pick(row: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: row[key] for key in keys if key in row}


def _outcome_payload(row: dict[str, Any], peers: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return build_outcome_review_pack(row, peers=peers)


def _validator_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "ValidatorTaxonomy.v1.review_input",
        **_pick(
            row,
            (
                "symbol",
                "timeframe",
                "family",
                "strategy_id",
                "outcome_class",
                "lite_status",
                "validation_status",
                "revalidation_status",
                "reason",
                "subreason",
                "n",
                "n_trades",
                "avg_net_pct",
                "test_avg_net_pct",
                "profit_factor",
                "max_drawdown_pct",
                "recovered_reason",
                "revisit_reason",
            ),
        ),
        "paper_only": True,
        "execution_allowed": False,
    }


def _source_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "ScannerEvent.v1.review_input",
        **_pick(
            row,
            (
                "scanner_event_id",
                "symbol",
                "instrument",
                "timeframe",
                "source",
                "reason",
                "mode",
                "timestamp",
                "movement_stats",
                "liquidity",
                "data_freshness",
                "context_refs",
            ),
        ),
        "paper_only": True,
        "execution_allowed": False,
    }


def _summary_path(private_root: Path) -> Path:
    return private_root / "reports" / "agent_role_review_cycle" / "summary.json"


def run_cycle(args: argparse.Namespace) -> dict[str, Any]:
    private_root = resolve_private_root(args.private_root)
    provider = _make_provider(args)
    training_rows = _read_jsonl_tail(
        private_root / "state" / "derived" / "paper_signal_training.jsonl",
        args.max_outcomes,
    )
    validator_rows = _load_validator_memory(private_root, args.max_validator)
    source_rows = _read_jsonl_tail(
        private_root / "state" / "lineage" / "scanner_events.jsonl",
        args.max_sources,
    )

    reviews = []
    for row in training_rows:
        source_ref = str(row.get("training_row_id") or row.get("paper_signal_id") or row.get("signal_id") or "")
        reviews.append(
            request_role_review(
                private_root,
                role_id="outcome_reviewer",
                source_ref=source_ref or f"outcome_{len(reviews)}",
                source_payload=_outcome_payload(row, training_rows),
                provider=provider,
            )
        )
        time.sleep(args.sleep_seconds)
    for row in validator_rows:
        source_ref = str(row.get("candidate_id") or row.get("uc_key") or row.get("symbol") or "")
        reviews.append(
            request_role_review(
                private_root,
                role_id="validator_reviewer",
                source_ref=source_ref or f"validator_{len(reviews)}",
                source_payload=_validator_payload(row),
                provider=provider,
            )
        )
        time.sleep(args.sleep_seconds)
    for row in source_rows:
        source_ref = str(row.get("scanner_event_id") or row.get("symbol") or "")
        reviews.append(
            request_role_review(
                private_root,
                role_id="source_trust_reviewer",
                source_ref=source_ref or f"source_{len(reviews)}",
                source_payload=_source_payload(row),
                provider=provider,
            )
        )
        time.sleep(args.sleep_seconds)

    accepted = sum(1 for row in reviews if row.accepted)
    summary = {
        "schema": "AgentRoleReviewCycleSummary.v1",
        "created_at": utc_now(),
        "provider": getattr(provider, "name", args.provider),
        "configured": bool(getattr(provider, "configured", False)),
        "inputs": {
            "outcomes": len(training_rows),
            "validator": len(validator_rows),
            "sources": len(source_rows),
        },
        "outcome_learning": learning_summary(training_rows),
        "reviews": len(reviews),
        "accepted": accepted,
        "rejected": len(reviews) - accepted,
        "review_summary": review_summary(private_root),
        "private_path_label": "strategy-lab/reports/agent_role_review_cycle/summary.json",
        "paper_only": True,
        "execution_allowed": False,
    }
    path = _summary_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--provider", default="alibaba")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key-env", default="ALIBABA_API_KEY")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--rate-rub-per-1k", type=float, default=DEFAULT_RATE_RUB_PER_1K)
    parser.add_argument("--max-outcomes", type=int, default=3)
    parser.add_argument("--max-validator", type=int, default=2)
    parser.add_argument("--max-sources", type=int, default=2)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_cycle(args)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "agent_role_review_cycle: "
            f"provider={summary['provider']} configured={summary['configured']} "
            f"inputs={summary['inputs']} reviews={summary['reviews']} "
            f"accepted={summary['accepted']} rejected={summary['rejected']} "
            f"path={summary['private_path_label']}"
        )


if __name__ == "__main__":
    main()
