from __future__ import annotations

import fnmatch
import subprocess
import sys


DENY_PATTERNS = [
    ".env",
    "*.env",
    "*.pem",
    "*.key",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-wal",
    "*.sqlite-shm",
    "*.db",
    "*.db-*",
    "*.duckdb",
    "*.log",
    "*.jsonl",
    "*.pkl",
    "*.parquet",
    "*.feather",
    "*.xlsx",
    "logs/*",
    "logs_archive/*",
    ".codex/*",
    ".codex-remote-attachments/*",
    ".agents/*",
    ".internal/*",
    "**/private_root/*",
    "**/raw-model-responses/*",
    "**/private-traces/*",
    "**/private-notes/*",
    "**/canary_runs/*",
    "data/scout/*.sqlite*",
    "scripts/subscriptions.json",
    "scripts/journal.xlsx",
    "scripts/pattern_db.csv",
    "scripts/backtest_candle_cache*.pkl",
    "scripts/backtest_mark_index_cache*.pkl",
    "scripts/analysis/backtest_runs/*",
    "scripts/backtest/indicator_research_report.md",
    "docs/qwen_*.txt",
    "docs/video*_transcript_*.txt",
    "docs/geometry/*.png",
    "docs/geometry/cases/*.png",
    "scripts/backtest_runs/*",
    "scripts/backtest/cache/*",
    "scripts/backtest/results/*",
    "scripts/analysis_output/*",
    "scripts/tape/*",
    "scripts/tg_temp/*",
    "reports/*",
]

ALLOW_PATTERNS = [
    ".env.example",
    "scripts/backtest_runs/.gitkeep",
    "scripts/backtest/backtest_runs/.gitkeep",
    "scripts/ws/cache/.gitkeep",
]


def git_ls_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def main() -> int:
    violations = [
        path
        for path in git_ls_files()
        if matches_any(path, DENY_PATTERNS) and not matches_any(path, ALLOW_PATTERNS)
    ]
    if not violations:
        print("tracked artifact guard: ok")
        return 0

    print("tracked artifact guard: blocked tracked private/runtime artifacts", file=sys.stderr)
    for path in violations:
        print(f" - {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
