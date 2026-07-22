# Project Brain And Main-Chat Contract

Status: **SHADOW IMPLEMENTATION**. The project brain proposes context; it does
not change code, Git, runtime, delivery, or authority on its own.

The graph, private-store implementation, router, and hook adapters exist in
this repository. They are not installed into Codex or another chat client.
`configs/project_brain/hooks.json` is a reviewed adapter catalog, not an active
hook configuration. Automatic memory maintenance and automatic SESSION/TASK
updates are not enabled.

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

The shadow `PreCompact` and `Stop` adapters can append a verified private delta
only when a separate external manifest explicitly allows `write_memory` for
the private project-brain store. Without it they return a denial and write
nothing. `PostCompact` and `SessionStart` return only project identity, exact
SHA, authority requirements, active-work pointers, decisions, open questions,
and evidence references. The transcript is passive evidence, never canonical
storage. SESSION/TASK remain manual compact documents and are marked stale when
their declared SHA differs from Git; no adapter silently rewrites them.

Large tool output and raw user prompts stay outside the packet; only a safe
intent summary, content hash, safe evidence pointer, timestamp, and safe result
summary are retained. The shared public-artifact scanner checks keys and nested
string values before memory or packet construction and rejects credential,
cookie, private-key, recipient-like, `.env`, and protected-identity content
without echoing the value. Changed or removed source nodes invalidate records
bound to their old content hashes.

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

Installing real client hooks is a later, separate operation: review the client
hook format, install explicitly, repeat shadow observation, then request an
authoritative-promotion decision. Hook installation or promotion must not be
combined with merge of this repair.
