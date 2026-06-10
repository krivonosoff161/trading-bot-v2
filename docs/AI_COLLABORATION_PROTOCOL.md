# AI Collaboration Protocol

Updated: 2026-06-10

Purpose: describe how this project uses remote coding agents without letting
them overwrite local context, secrets, runtime data, or active scanner direction.

## Current Workflow

```
User defines the task
    ↓
Local Codex prepares a precise remote-agent task
    ↓
User sends it to Claude/Qwen/another remote agent
    ↓
Remote agent returns a report, patch, branch, or commit summary
    ↓
User brings the result back here
    ↓
Local Codex reviews against repo state, tests, docs and local-only data
```

## When To Use A Remote Agent

- scanner calibration tasks;
- review of `GO / WATCH / NO_GO` boundaries;
- Telegram card wording and dedup logic;
- source/layer quality reporting;
- outcome-resolver reliability;
- docs/code consistency review;
- small scanner tests or refactors with clear file limits.

## When Not To Use A Remote Agent

- task requires raw local logs, `.env`, `.pkl`, SQLite database, or `SESSION.md`;
- task requires API keys or Telegram tokens;
- task touches live order execution or `AUTO_TRADE`;
- task asks for real-money trading decisions;
- task relies on private local data that has not been summarized safely.

## Remote-Agent Task Template

```
REMOTE AGENT TASK:

Context files:
- docs/AI_CONTEXT.md
- docs/REMOTE_DATA_MANIFEST.md
- CURRENT_STATE.md
- README.md
- ROADMAP.md
- SCANNER_SPEC.md
- TASK.md
- [specific files for this task]

Mode: READ-ONLY | BRANCH-ONLY
(READ-ONLY = analysis/report only)
(BRANCH-ONLY = code/docs changes in a feature branch or unpushed local commit)

Task:
[exact task]

Expected output:
[markdown report / patch summary / test results / exact changed files]

Do not:
- request or print `.env`, API keys, Telegram tokens, or raw private logs
- assume ignored files are absent
- touch live order execution or `AUTO_TRADE`
- claim profitability from paper/research metrics
- commit directly to main unless explicitly instructed
```

## Example Current Tasks

```
REMOTE AGENT TASK:

Context files:
- docs/AI_CONTEXT.md
- CURRENT_STATE.md
- README.md
- ROADMAP.md
- SCANNER_SPEC.md
- src/scout/scanner_v0.py
- src/scout/agents/chief.py
- src/scout/resolve_outcomes.py

Mode: BRANCH-ONLY

Task:
Review the post-v0.6 scanner calibration behavior:
1. Check whether `GO` / `WATCH` Telegram gating is isolated in `should_send_to_channel`.
2. Check that `NO_GO` still reaches logs/training data.
3. Review whether card text remains layer-specific and trader-readable.
4. Review whether `resolve_outcomes.py --limit N` is safe and resumable.
5. Suggest the next evidence-based threshold changes, but do not hard-code new
   thresholds without local data.

Expected output:
Markdown review with risks, exact file references, and any focused tests you
recommend. Do not write code in this task.

Do not:
- touch live trading/order paths
- request raw logs or secrets
- change unrelated frozen WebSocket engines
```

```
REMOTE AGENT TASK:

Context files:
- docs/AI_CONTEXT.md
- docs/REMOTE_DATA_MANIFEST.md
- src/scout/source_quality_report.py
- src/scout/router.py
- tests/test_source_quality_report.py

Mode: READ-ONLY

Task:
Review source-quality reporting for the current scanner. Identify missing
aggregations that would help explain excessive NO_GO decisions by source, layer,
asset, phase, lead class, and chief-called status.

Expected output:
Markdown report with concrete proposed metrics and exact files that would need
changes. Do not write code in this task.
```

## Context Files To Include

| File | Purpose |
|---|---|
| `docs/AI_CONTEXT.md` | Current architecture, boundaries and agent context. |
| `docs/REMOTE_DATA_MANIFEST.md` | Local-only data map. |
| `CURRENT_STATE.md` | Short operational status. |
| `ROADMAP.md` | Current development sequence. |
| `SCANNER_SPEC.md` | Scanner design and as-built notes. |
| `TASK.md` | Local handoff. |

## Packing Code For A Remote Agent

When a remote agent cannot access the repo directly, package only the relevant
files:

```
npx repomix --include "src/scout/**,tests/test_scanner_*.py,tests/test_source_quality_report.py,README.md,CURRENT_STATE.md,ROADMAP.md,SCANNER_SPEC.md,docs/AI_CONTEXT.md,docs/REMOTE_DATA_MANIFEST.md"
```

Do not run repomix without `--include`; it will package too much historical
code. Never include `.env`, raw logs, private data, or ignored local caches.

## Integrating Remote-Agent Output

1. Bring the report or patch back to local Codex.
2. Check changed files against current docs and local-only data assumptions.
3. Run focused tests, at minimum scanner tests affected by the change.
4. Keep code, docs and `TASK.md` consistent.
5. Commit only after user approval.
6. Update `docs/AI_CONTEXT.md` if architecture or active workflow changed.
