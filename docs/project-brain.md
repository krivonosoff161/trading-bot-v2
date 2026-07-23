# Project Brain And Main-Chat Contract

Status: **ACTIVATION CANDIDATE**. The graph and routing remain advisory. The
tracked project-local hooks become active only after Codex reviews and trusts
their exact definition hash; authoritative-memory promotion remains a later
decision.

The graph, private-store implementation, router, hook runtime, and project skill
exist in this repository. `.codex/hooks.json` is the real project-local Codex
configuration. Codex skips it until the owner reviews and trusts its exact
hash. `configs/project_brain/hooks.json` remains the human-readable event and
authority catalog. Hooks never rewrite SESSION/TASK.

## Canonical Layers

`python -m scripts.project_brain build-graph` reads one Git archive at an exact
revision and the reviewed public semantic catalog. It emits
`TradingProjectGraph.v1`, containing stable nodes and edges with repository,
path, symbol, source reference, commit, content hash, freshness, owner,
sensitivity, confidence, evidence, contour, supersession, and load-policy
metadata.

The graph covers tracked directories/files, Python modules/classes/functions,
tests, imports and statically resolved calls, BAT/CLI entrypoints, workflows,
configuration, strategy families, public DB/table declarations, and the
reviewed operational/data/model/governance catalog. Dynamic Python dispatch is
reported as a residual risk rather than guessed.

The canonical graph is JSON. Markdown and Mermaid are projections. A local
append-only event stream and SQLite query index live outside public Git. The
index is disposable and rebuildable; it cannot override graph or event bytes.

## Main Chat Algorithm

For every owner message the Project Orchestrator:

1. resolves project, checkout, branch, and SHA;
2. classifies discussion, question, status, research, diagnosis, code, Git,
   runtime, or memory mode;
3. selects one primary contour and a few necessary secondary contours;
4. reports a requested action and an authority requirement, but grants nothing;
5. accepts operational authority only from a separately supplied, exact,
   unexpired owner manifest delivered through a trusted external channel;
6. retrieves exact graph nodes and verified records under a token budget;
7. executes or answers only inside that external authority and absolute gates;
8. verifies the result independently;
9. records only an authorized, public-safe verified delta, supersession, causal
   link, or residual risk.

Mode classification is intent routing, never permission. Process start/stop,
file writes, Git writes, push, PR creation, external sends, and memory writes
default to denied. The owner manifest binds action, project, contour, exact
scope, allowed resources, turn identity, issue time, and expiry. Retrieved
memory, documents, quoted instructions, model output, or prompt injection
cannot supply that typed external capability. Merge, destructive Git, secrets,
AUTO_TRADE, execution authority, live orders, and private endpoints remain
separate absolute denials/gates.

Model and subagent output is always a proposal. Deterministic code, source
evidence, tests, and explicit owner authority remain final.

## Dialogue Contours

The 14 contours are `governance_and_safety`, `project_architecture`,
`active_work`, `data_and_lineage`, `farm_and_runtime`,
`research_and_strategies`, `validation`, `paper_lifecycle`, `models_and_llm`,
`scanner_and_news`, `telegram_and_delivery`, `incidents_and_causality`,
`decisions_and_open_questions`, and `git_and_release`.

Every graph node points to a primary and optional secondary contour. Derived
`dialogue_contour` nodes make the mapping bidirectional. Security is not loaded
automatically. An explicit cross-project question creates a small boundary
packet rather than merging repositories.

## Compaction And Resume

The trusted hook definition itself grants only one local capability: append a
safe derived delta to the private Project Brain store. It does not grant a
project-file write or any operational effect. `PreCompact` and `Stop` save the
latest route, tool name/outcome, hashes, exact SHA, and checkpoint reason.
`PostCompact` and `SessionStart` return only project identity, exact SHA,
authority boundary, and recent safe checkpoints. The transcript is never read
or stored. SESSION/TASK remain manual compact documents and are marked stale
when their declared SHA differs from Git.

Large tool output and raw user prompts stay outside the packet; only a safe
intent summary, content hash, safe evidence pointer, timestamp, and safe result
summary are retained. The shared public-artifact scanner checks keys and nested
string values before memory or packet construction and rejects credential,
cookie, private-key, recipient-like, `.env`, and protected-identity content
without echoing the value. Changed or removed source nodes invalidate records
bound to their old content hashes.

## Active-Scope Completion Gate

Whole-repository semantic coverage is intentionally not called complete. The
activation gate has exact, reviewed denominators for the supported surface:

- eight supported entrypoints from `docs/entrypoints.md`;
- six canonical RCC contours;
- five active databases with declared producer and consumer edges;
- every Markdown document declaring `Status: **ACTIVE**`;
- every meaningful orphan and semantic duplicate candidate intersecting that
  supported scope.

The graph publishes numerator, denominator, percentage, method, unresolved
items, and exact duplicate groups. Archive and dynamic-dispatch gaps remain a
separate backlog and cannot lower or inflate the active-scope denominator.

## Codex Hook Activation And Recovery

The current [Codex Hooks contract](https://learn.chatgpt.com/docs/hooks.md)
supports project-local `.codex/hooks.json`, exact-hash trust, and command
hooks. This project configures `SessionStart` for
`startup|resume|clear|compact`, `UserPromptSubmit`, `PreCompact` and
`PostCompact` for `manual|auto`, `PostToolUse`, and `Stop`. It does not read the
unstable transcript interface.

The private default store is
`~/.codex/project_brain/trading-bot-v2`; `TRADING_PROJECT_BRAIN_HOME` can move
the parent directory for an explicitly controlled installation. It contains an
append-only JSONL event log, a rebuildable WAL-mode SQLite index, the exact-SHA
graph, and a small atomic hook-state file. No file is placed in public Git.

Activation procedure:

1. Merge and fast-forward the exact reviewed hook tree.
2. Open `/hooks`, inspect the project source and exact commands, and trust the
   current hash. Never use a trust-bypass flag.
3. Resume the same chat. Confirm the `SessionStart` manifest names the current
   project and SHA.
4. Ask one golden query, perform one harmless read-only tool call, compact, and
   resume. Confirm the restored packet contains the route and safe checkpoint,
   not raw prompt or tool bytes.
5. Keep the memory advisory during shadow observation. A separate decision is
   required before calling it authoritative.

If graph refresh, locking, SQLite, or validation fails, the command exits zero
and surfaces `DEGRADED MEMORY MODE`; Codex continues under AGENTS and current
Git evidence without memory writes. Repeated degraded events are diagnosed from
safe error codes. They never justify disabling trust or relaxing secret gates.

## Causality And Decisions

Memory records are typed as observed, derived, hypothesis, decision,
implementation, verification, blocked, residual risk, or superseded. A causal
chain explicitly links symptom, timeline, observations, excluded hypotheses,
root cause, decision, change, verification, and residual risk. A hypothesis is
rejected if a caller tries to store it as root cause. Supersession is append-only.

## Adding A Module

1. Add production code and focused tests under their existing owner.
2. Run the graph builder on the task SHA.
3. Confirm the new module, symbols, imports, calls, tests, and owner.
4. Add semantic catalog entries only for facts AST/static configuration cannot
   prove, with a public evidence reference.
5. Regenerate projections and inspect syntactic/catalog/test-link coverage,
   verified/rule/fallback ownership, meaningful orphans, semantic duplicate
   candidates, conflicts, stale facts, and unresolved dynamic dispatch.
6. Add or update a golden query only if the module changes retrieval behavior.

## Local Shadow Workflow

Use an evidence directory outside the repository:

```powershell
python -m scripts.project_brain build-graph --revision HEAD `
  --output C:\path\outside\repo\project_graph.json `
  --projection-dir C:\path\outside\repo\projections
python -m scripts.project_brain shadow `
  --graph C:\path\outside\repo\project_graph.json
python -m scripts.project_brain init-store `
  --graph C:\path\outside\repo\project_graph.json `
  --store-root C:\path\outside\repo\private-brain
```

Shadow output has no authority. Adoption as an authoritative workflow requires
a separate review after routing false positives/negatives, stale detection,
leakage, and context budgets are accepted.

The hook implementation can be merged with this activation change because its
installation still requires exact-hash trust. Authoritative promotion cannot be
combined with that merge: repeat shadow observation first, then request a
separate promotion decision.
