"""Run and verify one bounded paper/research end-to-end cycle.

This is an operational smoke, not a trading launcher. It runs the canonical farm
cycle with worker/validation/paper/paper-signals enabled, optionally calls the
local bounded calculator advisor, and then verifies private Strategy Lab
artifacts. It never touches .env, AUTO_TRADE, private order endpoints, or old
order-capable main.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.strategy_lab.operational_health import collect as collect_health  # noqa: E402
from src.research_lab.lineage_contract import cycle_links_path, scanner_events_path  # noqa: E402
from src.research_lab.market_data_packet import packet_index_path as data_packet_index_path  # noqa: E402
from src.research_lab.feature_packet import packet_index_path as feature_packet_index_path  # noqa: E402
from src.research_lab.paths import DEFAULT_PRIVATE_ROOT  # noqa: E402

SCHEMA = "PaperResearchE2ESmoke.v1"
DERIVED_REQUIRED = {
    "paper_signals": "paper_signals.jsonl",
    "main_paper_instructions": "main_paper_instructions.json",
    "main_paper_consumed": "main_paper_consumed.json",
    "main_paper_runtime_queue": "main_paper_runtime_queue.json",
    "main_paper_runtime_observation": "main_paper_runtime_observation.json",
    "paper_telegram_preview": "paper_telegram_preview.json",
    "paper_telegram_delivery": "paper_telegram_delivery.json",
    "paper_signal_training": "paper_signal_training.jsonl",
}


def build_farm_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "scripts.strategy_lab.farm_loop",
        "--once",
        "--apply",
        "--run-worker",
        "--run-validation",
        "--run-paper",
        "--run-paper-signals",
        "--enrich-funding",
        "--enrich-oi",
        "--backend",
        args.backend,
        "--provider",
        args.provider,
        "--pfr-db-path",
        str(args.pfr_db_path),
        "--paper-signals-max-observe",
        str(args.paper_signals_max_observe),
        "--paper-signals-max-pfr-scan",
        str(args.paper_signals_max_pfr_scan),
        "--paper-signals-pfr-reserved",
        str(args.paper_signals_pfr_reserved),
        "--main-paper-runtime-limit",
        str(args.main_paper_runtime_limit),
        "--max-plan-events",
        str(args.max_plan_events),
        "--max-prepares",
        str(args.max_prepares),
        "--max-enrich",
        str(args.max_enrich),
        "--max-sweeps",
        str(args.max_sweeps),
        "--max-worker-jobs",
        str(args.max_worker_jobs),
        "--max-validations",
        str(args.max_validations),
        "--max-paper-cards",
        str(args.max_paper_cards),
        "--data-days",
        str(args.data_days),
        "--private-root",
        str(args.private_root),
        "--night-mode",
    ]
    if args.no_discovery_refresh:
        cmd.append("--no-discovery-refresh")
    if args.run_calculator_advisor:
        cmd.extend(
            [
                "--run-calculator-advisor",
                "--calculator-provider",
                args.calculator_provider,
                "--calculator-model",
                args.calculator_model,
                "--calculator-base-url",
                args.calculator_base_url,
                "--calculator-timeout",
                str(args.calculator_timeout),
                "--calculator-advisor-max-calls",
                str(args.calculator_advisor_max_calls),
            ]
        )
    return cmd


def run_cycle(args: argparse.Namespace) -> dict[str, Any]:
    cmd = build_farm_command(args)
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=args.timeout_seconds,
        check=False,
    )
    return {
        "started_at_epoch": started,
        "returncode": proc.returncode,
        "command": " ".join(cmd),
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-60:]),
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-40:]),
    }


def verify_cycle(
    private_root: Path,
    *,
    require_calculator_accepted: bool = False,
    since_epoch: float | None = None,
) -> dict[str, Any]:
    private_root = Path(private_root)
    derived_root = private_root / "state" / "derived"
    checks: list[dict[str, Any]] = []
    checks.extend(
        [
            _jsonl_check("scanner_events", scanner_events_path(private_root), min_rows=1),
            _jsonl_check("data_packets", data_packet_index_path(private_root), min_rows=1),
            _jsonl_check("feature_packets", feature_packet_index_path(private_root), min_rows=1),
            _jsonl_check("cycle_links", cycle_links_path(private_root), min_rows=1),
            _jsonl_check(
                "calculator_advice",
                private_root / "state" / "llm_advice" / "calculator_advice.jsonl",
                min_rows=1,
            ),
        ]
    )
    for name, filename in DERIVED_REQUIRED.items():
        path = derived_root / filename
        checks.append(_jsonl_check(name, path, min_rows=1) if path.suffix == ".jsonl" else _json_snapshot_check(name, path))
    checks.append(_training_safety_check(derived_root / "paper_signal_training.jsonl"))
    checks.append(_telegram_card_check(derived_root / "paper_telegram_preview.json"))
    if require_calculator_accepted:
        checks.append(
            _calculator_acceptance_check(
                private_root / "state" / "llm_advice" / "calculator_advice.jsonl",
                since_epoch=since_epoch,
            )
        )
    health = collect_health(private_root=private_root, pfr_db_path=private_root / "state" / "strategy_lab.sqlite")
    blocking = (health.get("operator_next_actions") or {}).get("blocking") or []
    checks.append({"name": "operational_health_blocking", "ok": not blocking, "blocking": blocking})
    ok = all(bool(item.get("ok")) for item in checks)
    return {
        "schema": SCHEMA,
        "ok": ok,
        "checks": checks,
        "paper_only": True,
        "execution_allowed": False,
        "private_root_label": "strategy-lab",
    }


def _jsonl_check(name: str, path: Path, *, min_rows: int) -> dict[str, Any]:
    rows = 0
    invalid = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            rows += 1
            try:
                json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
    return {
        "name": name,
        "path_label": _label(path),
        "exists": path.exists(),
        "rows": rows,
        "invalid_json": invalid,
        "ok": path.exists() and rows >= min_rows and invalid == 0,
    }


def _json_snapshot_check(name: str, path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    error = ""
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            error = type(exc).__name__
    count = int(data.get("items") and len(data["items"]) or data.get("rows") or data.get("rendered") or 0)
    return {
        "name": name,
        "path_label": _label(path),
        "exists": path.exists(),
        "rows_or_items": count,
        "error": error,
        "ok": path.exists() and not error and count > 0,
    }


def _training_safety_check(path: Path) -> dict[str, Any]:
    rows = 0
    paper_false = 0
    execution_true = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rows += 1
            paper_false += int(row.get("paper_only") is not True)
            execution_true += int(row.get("execution_allowed") is not False)
    return {
        "name": "training_safety",
        "rows": rows,
        "paper_only_false": paper_false,
        "execution_allowed_true": execution_true,
        "ok": rows > 0 and paper_false == 0 and execution_true == 0,
    }


def _telegram_card_check(path: Path) -> dict[str, Any]:
    bad = 0
    rows = 0
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("items") or []:
            rows += 1
            text = str(item.get("text") or "")
            has_title = "Бумажный сигнал:" in text or "Кандидат фермы:" in text or "Paper-сетап:" in text
            if not has_title or "Автоисполнение выключено." not in text:
                bad += 1
            if any(marker in text for marker in ("СЃ", "С‚", "Р°", "Рµ", "вЂ", "Â")):
                bad += 1
    return {"name": "telegram_card_human_readable", "rows": rows, "bad": bad, "ok": rows > 0 and bad == 0}


def _calculator_acceptance_check(path: Path, *, since_epoch: float | None = None) -> dict[str, Any]:
    accepted = 0
    rows = 0
    latest_problem = ""
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if since_epoch is not None and _created_epoch(row.get("created_at")) < since_epoch:
                continue
            rows += 1
            accepted += int(row.get("accepted") is True)
            if row.get("problems"):
                latest_problem = str((row.get("problems") or [""])[0])
    return {
        "name": "calculator_accepted",
        "rows_since_start": rows,
        "accepted_since_start": accepted,
        "latest_problem": latest_problem,
        "ok": accepted > 0,
    }


def _created_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _label(path: Path) -> str:
    parts = path.parts
    if "strategy-lab" in parts:
        idx = parts.index("strategy-lab")
        return "/".join(parts[idx:])
    return path.name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-root", type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument("--pfr-db-path", type=Path, default=DEFAULT_PRIVATE_ROOT / "state" / "strategy_lab.sqlite")
    parser.add_argument("--skip-run", action="store_true", help="verify existing private artifacts only")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--provider", default="okx-public")
    parser.add_argument("--max-plan-events", type=int, default=2)
    parser.add_argument("--max-prepares", type=int, default=1)
    parser.add_argument("--max-enrich", type=int, default=1)
    parser.add_argument("--max-sweeps", type=int, default=1)
    parser.add_argument("--max-worker-jobs", type=int, default=1)
    parser.add_argument("--max-validations", type=int, default=3)
    parser.add_argument("--max-paper-cards", type=int, default=20)
    parser.add_argument("--data-days", type=int, default=30)
    parser.add_argument("--paper-signals-max-observe", type=int, default=10)
    parser.add_argument("--paper-signals-max-pfr-scan", type=int, default=10)
    parser.add_argument("--paper-signals-pfr-reserved", type=int, default=1)
    parser.add_argument("--main-paper-runtime-limit", type=int, default=30)
    parser.add_argument("--no-discovery-refresh", action="store_true")
    parser.add_argument("--no-calculator", dest="run_calculator_advisor", action="store_false")
    parser.set_defaults(run_calculator_advisor=True)
    parser.add_argument("--calculator-provider", default="ollama")
    parser.add_argument("--calculator-model", default="calculator")
    parser.add_argument("--calculator-base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--calculator-timeout", type=float, default=120.0)
    parser.add_argument("--calculator-advisor-max-calls", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cycle = {"skipped": True}
    if not args.skip_run:
        cycle = run_cycle(args)
    verification = verify_cycle(
        args.private_root,
        require_calculator_accepted=args.run_calculator_advisor,
        since_epoch=cycle.get("started_at_epoch") if not args.skip_run else None,
    )
    report = {
        "schema": SCHEMA,
        "cycle": cycle,
        "verification": verification,
        "ok": (cycle.get("returncode", 0) == 0) and verification["ok"],
        "paper_only": True,
        "execution_allowed": False,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"paper_research_e2e_smoke ok={report['ok']} returncode={cycle.get('returncode', 0)}")
        for check in verification["checks"]:
            print(f"  {'OK' if check.get('ok') else 'FAIL'} {check.get('name')}: {check}")
        if cycle.get("stdout_tail"):
            print("\n--- farm stdout tail ---")
            print(cycle["stdout_tail"])
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
