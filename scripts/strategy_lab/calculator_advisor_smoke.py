"""Run one bounded calculator-advisor smoke over a FeaturePacket."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research_lab.calculator_advisor import request_calculator_advice  # noqa: E402
from src.research_lab.feature_packet import latest_feature_packet_path, load_feature_packet  # noqa: E402
from src.research_lab.llm_provider import load_provider  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402


def _provider_env(args: argparse.Namespace) -> dict[str, str]:
    env = dict(os.environ)
    if args.provider:
        env["STRATEGY_LAB_LLM_ENABLED"] = "1"
        env["STRATEGY_LAB_LLM_PROVIDER"] = args.provider
    if args.model:
        env["STRATEGY_LAB_LLM_MODEL_CHEAP"] = args.model
    if args.base_url:
        env["STRATEGY_LAB_LLM_BASE_URL"] = args.base_url
    if args.timeout:
        env["STRATEGY_LAB_LLM_TIMEOUT"] = str(args.timeout)
    return env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--feature-packet", type=Path, default=None)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--timeout", type=float, default=0.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    packet_path = args.feature_packet or latest_feature_packet_path(args.private_root)
    if packet_path is None:
        raise SystemExit("no feature packet found; run paper-signals apply smoke first")
    packet = load_feature_packet(packet_path)
    provider = load_provider(_provider_env(args))
    advice = request_calculator_advice(args.private_root, packet, provider)
    payload = {
        "schema": "calculator_advisor_smoke.v1",
        "feature_packet_id": packet.feature_packet_id,
        "advisor_ref": advice.advisor_ref,
        "accepted": advice.accepted,
        "problems": advice.problems,
        "provider": advice.provider,
        "model": advice.model,
        "paper_only": advice.paper_only,
        "execution_allowed": advice.execution_allowed,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "calculator_advisor_smoke: "
            f"accepted={payload['accepted']} provider={payload['provider']} "
            f"feature_packet_id={payload['feature_packet_id']} problems={payload['problems']}"
        )


if __name__ == "__main__":
    main()
