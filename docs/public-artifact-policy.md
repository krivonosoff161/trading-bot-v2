# Public Artifact Policy

Status: **ACTIVE**. Updated 2026-07-10.

Before staging a file, classify it by content rather than its extension.

## May Be Public

- source code, tests, configuration templates, and small deterministic fixtures;
- architecture, operation, and safety documentation;
- synthetic examples and sanitized aggregate methodology;
- sanitized retrospective narratives in `docs/legacy-evidence/` that state
  their historical status and limitations without reproducing raw calculations,
  parameters, per-trade results, or strategy rankings;
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
python scripts/ci/check_supply_chain_policy.py
python scripts/ci/check_tracked_artifacts.py
git diff --check
git status --short
```

The artifact guard blocks common runtime and credential paths. It is a safety
net, not content classification. A path that passes the guard can still be
private and must not be committed.

The supply-chain guard is an offline public-repository check. It blocks mutable
external GitHub Action references in workflows, requires CI to install from the
exact `requirements-ci.txt` reconstruction input, verifies the tracked
`requirements-ci.sha256` digest for that input, and scans tracked text files for
secret-like assignments while reporting only path, line, and secret type. It
does not replace repository settings, GitHub dependency review enforcement,
SBOM generation, package archive hash provenance, or maintainer review.

## Existing Historical Material

Public Git heads were rewritten on 2026-07-10 after an encrypted private backup
was verified. Fresh clones do not include the removed raw model packs or
geometry outputs. GitHub pull-request refs and external clones can retain older
references, so no raw artifact may be reintroduced as a substitute for a
sanitized public narrative.
