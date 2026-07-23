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

The otherwise private `.codex/` and `.agents/` namespaces have exactly two
reviewed source exceptions: `.codex/hooks.json` and
`.agents/skills/project-brain/SKILL.md`. The guard still rejects every other
file under those namespaces, including local hook state, trust data, memories,
outputs, and client configuration.

The supply-chain guard is an offline public-repository check. It blocks mutable
external GitHub Action references in workflows, requires CI to install with
`--require-hashes` from the pip-compile-generated transitive
`requirements-ci.txt`, verifies that every direct `requirements-ci.in` entry is
present, requires SHA-256 archive hashes for every locked distribution, verifies
the tracked `requirements-ci.sha256` lock identity, and scans tracked text files
for secret-like assignments while reporting only path, line, and secret type.

Hash-checked reconstruction does not replace repository settings, GitHub
dependency-review enforcement, license review, SBOM generation, package
signature/provenance attestations, vulnerability response, or maintainer review.
An offline installation also requires a target-platform wheelhouse populated and
verified in advance; the wheelhouse is local evidence and is never committed.

## Existing Historical Material

Public Git heads were rewritten on 2026-07-10 after an encrypted private backup
was verified. Fresh clones do not include the removed raw model packs or
geometry outputs. GitHub pull-request refs and external clones can retain older
references, so no raw artifact may be reintroduced as a substitute for a
sanitized public narrative.
