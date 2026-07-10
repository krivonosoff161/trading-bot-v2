# Public Artifact Policy

Status: **ACTIVE**. Updated 2026-07-10.

Before staging a file, classify it by content rather than its extension.

## May Be Public

- source code, tests, configuration templates, and small deterministic fixtures;
- architecture, operation, and safety documentation;
- synthetic examples and sanitized aggregate methodology;
- public diagrams that contain no private market history, per-trade results,
  candidate rankings, credentials, or raw model output.

## Must Stay Local Or Private

- `.env`, tokens, exchange/provider/Telegram credentials, and private IDs;
- raw market data, tick/candle caches, SQLite state, logs, journals, delivery
  audit, subscriptions, and screenshots from real operation;
- strategy calculations, parameter sweeps, candidate rankings, trade-level
  charts/results, and private paper/live rows;
- raw prompts, model responses, transcripts, downloaded media, or personal
  communications;
- generated reports that reveal any of the above.

## Required Checks

```powershell
python scripts/ci/check_tracked_artifacts.py
git diff --check
git status --short
```

The artifact guard blocks common runtime and credential paths. It is a safety
net, not content classification. A path that passes the guard can still be
private and must not be committed.

## Existing Historical Material

The current branch removes raw model packs and geometry outputs from the public
index while keeping local copies ignored. Historical Git cleanup is not done
automatically: it requires a separate approved procedure with an encrypted
private backup and a review of rewritten public refs.
