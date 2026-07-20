"""Run a private provider bench for Strategy Lab LLM agent roles.

This CLI can use Alibaba/OpenAI-compatible providers or local Ollama. It writes
raw role results only under the private Strategy Lab root and prints a sanitized
summary. It never reads .env directly, never sends Telegram, and never enables
execution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.runtime_root import load_runtime_dotenv  # noqa: E402

if __name__ == "__main__":
    load_runtime_dotenv(ROOT)

from src.research_lab.agent_role_registry import role_registry_summary  # noqa: E402
from src.research_lab.lineage_contract import append_jsonl, utc_now  # noqa: E402
from src.research_lab.llm_provider import (  # noqa: E402
    DEFAULT_RATE_RUB_PER_1K,
    OllamaProposalProvider,
    OpenAICompatibleProvider,
    ProposalProvider,
)
from src.research_lab.llm_role_reviews import request_role_review  # noqa: E402
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


def _sample_cases() -> list[dict[str, Any]]:
    return [
        {
            "role_id": "outcome_reviewer",
            "source_ref": "sample_outcome_gave_back",
            "source_payload": {
                "schema": "TrainingRow.v2.sample",
                "symbol": "BICO-USDT-SWAP",
                "timeframe": "15m",
                "family": "early_tp_tactical",
                "result": "closed_paper",
                "net_pct": -1.2,
                "mfe_pct": 2.8,
                "mae_pct": -1.4,
                "capture": 0.0,
                "diagnosis": "bad_exit_gave_back",
                "paper_only": True,
                "execution_allowed": False,
            },
        },
        {
            "role_id": "validator_reviewer",
            "source_ref": "sample_validator_underpowered",
            "source_payload": {
                "schema": "ValidatorTaxonomy.v1.sample",
                "symbol": "AERO-USDT-SWAP",
                "timeframe": "4h",
                "family": "momentum_breakout",
                "validator_class": "underpowered",
                "hard_status": "NEEDS_MORE_DATA",
                "n": 4,
                "mean_net_pct": 1.1,
                "sidak_adjusted": False,
                "paper_only": True,
                "execution_allowed": False,
            },
        },
        {
            "role_id": "source_trust_reviewer",
            "source_ref": "sample_source_news",
            "source_payload": {
                "schema": "ScannerEvent.v1.sample",
                "source": "okx_announcements",
                "reason": "new_listing_or_event",
                "symbol": "TEST-USDT-SWAP",
                "later_outcome": "no_follow_through",
                "duplicate_sources": 1,
                "source_age_minutes": 14,
                "paper_only": True,
                "execution_allowed": False,
            },
        },
    ]


def _bench_path(private_root: Path) -> Path:
    return private_root / "reports" / "provider_bench" / "agent_role_provider_bench.jsonl"


def run_bench(args: argparse.Namespace) -> dict[str, Any]:
    private_root = resolve_private_root(args.private_root)
    provider = _make_provider(args)
    selected = [role.strip() for role in args.roles.split(",") if role.strip()]
    cases = [case for case in _sample_cases() if not selected or case["role_id"] in selected]
    rows = []
    for case in cases[: args.max_cases]:
        started = time.perf_counter()
        review = request_role_review(
            private_root,
            role_id=case["role_id"],
            source_ref=case["source_ref"],
            source_payload=case["source_payload"],
            provider=provider,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        row = {
            "schema": "ProviderBenchRow.v1",
            "created_at": utc_now(),
            "role_id": case["role_id"],
            "source_ref": case["source_ref"],
            "provider": review.provider,
            "model": review.model,
            "accepted": review.accepted,
            "problems": review.problems,
            "latency_ms": latency_ms,
            "paper_only": True,
            "execution_allowed": False,
        }
        append_jsonl(_bench_path(private_root), row)
        rows.append(row)
    accepted = sum(1 for row in rows if row["accepted"])
    summary = {
        "schema": "ProviderBenchSummary.v1",
        "provider": getattr(provider, "name", args.provider),
        "configured": bool(getattr(provider, "configured", False)),
        "rows": len(rows),
        "accepted": accepted,
        "rejected": len(rows) - accepted,
        "roles": sorted({row["role_id"] for row in rows}),
        "private_path_label": "strategy-lab/reports/provider_bench/agent_role_provider_bench.jsonl",
        "role_registry": role_registry_summary(),
        "paper_only": True,
        "execution_allowed": False,
    }
    summary_path = private_root / "reports" / "provider_bench" / "agent_role_provider_bench_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    parser.add_argument("--roles", default="")
    parser.add_argument("--max-cases", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_bench(args)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "agent_role_provider_bench: "
            f"provider={summary['provider']} configured={summary['configured']} "
            f"rows={summary['rows']} accepted={summary['accepted']} rejected={summary['rejected']} "
            f"path={summary['private_path_label']}"
        )


if __name__ == "__main__":
    main()
