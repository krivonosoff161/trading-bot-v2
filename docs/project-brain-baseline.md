# Project Brain Baseline And Gap Analysis

Status: **ACTIVE BASELINE**. Source commit:
`7c3fa06c0dfeb146ea4216c3c1762920096ca04a` (tree
`1fedb902a07f98208ba1403181aa88130e0a6af9`). The inventory reads tracked public
Git blobs and public contracts only. It does not inspect private database rows,
credentials, subscriber identities, raw model conversations, or runtime data.

## Baseline Inventory

| Surface | Purpose and authority | Writer / reader | Status and freshness | Reuse decision | Missing before this work |
|---|---|---|---|---|---|
| `ARCHITECTURE.md`, `CURRENT_STATE.md`, `ROADMAP.md` | Human architecture, support state, and sequence | Maintainers / humans and agents | Active; current Git is authoritative over prose | Evidence inputs and projections | No symbol, call, test, or freshness index |
| `docs/project-map.md` | Human directory and cross-repository ownership map | Maintainers / humans | Active, reviewed 2026-07-10 | Keep as concise projection | No machine graph, commit binding, coverage, or invalidation |
| `docs/entrypoints.md` | Complete BAT catalog and side-effect classes | Maintainers / operators and CI guard | Active | Canonical entrypoint classification input | Did not connect BAT files to Python modules, processes, leases, or DBs |
| `docs/farm_ownership_map.md` and `ownership.py` | Farm roles and durable process/lease/fence authority | Runtime code / operators and tests | Active domain source | Reuse ownership vocabulary and public schema | No repository-wide owner graph or dialogue load policy |
| `lineage_contract.py` | Stable IDs, content hashes, append-only paper/research links | Farm and exporters / dashboards and reports | Active domain ledger | Reuse deterministic ID/hash and evidence-reference patterns | Covers trading lineage, not code, decisions, dialogue, or Git |
| `graph_viewer.py`, `obsidian_graph.py`, `farm_obsidian.py` | Private candidate, outcome, and lineage visualization | Explicit diagnostics / humans | Active diagnostic/domain projections | Preserve; do not make a second viewer | Not a source/code/process/policy graph; reads private derived state |
| `local_model_context.py` | Three-document bounded public RAG for calculator advisors | Deterministic builder / local advisory model | Active, `LocalModelContext.v1` | Reuse bounded retrieval, document hashes, and tool-effect labels | No active-work, Git, authority, causality, or stale-state recovery |
| `llm_invocation_ledger.py` | Provider-call preflight, dedupe, endpoint identity, usage audit | Advisory roles / status reports | Active private control ledger | Reference through metadata-only nodes | Explicitly not model or project memory |
| `setup_outcome_memory.py` | Derived setup/outcome gate and research digest | Farm / planners and advisory prompts | Active private domain projection | Keep as `research_and_strategies` input summary | Cannot contain project or dialogue truth |
| `micro_memory.py` | Public-orderbook research outcome summary | Explicit research command / analysts | Active private domain projection | Keep isolated, summary-only | Not orchestration memory |
| `research_session.py` | Latest bounded research-session report | Explicit research CLI / dashboard | Active private run artifact | Evidence pointer only | Single latest report; no causal, decision, Git, or resume ledger |
| `search_trial_evidence.py` | Immutable search-family and execution identity | Farm / hard validator | Active validation evidence | Link as a first-class evidence node | Does not describe project architecture or dialogue state |
| `project_snapshot.py` | Operational human snapshot | Explicit diagnostic / operator | Active diagnostic | Reuse process classification concepts only | Runtime status is not a project knowledge graph |
| `SESSION.md` and `TASK.md` | Compact manual continuity checkpoint | Agents / next session | Local and deliberately ignored; at Phase 0 they named old SHA `adaf407...` while Git was `7c3fa06...` | Keep manual and detect staleness | No automatic commit/hash invalidation; stale prose could look current |
| `.agents/skills/` and local Codex archives | Local workflow hints and passive history | Local clients / agents | Ignored and non-public | Backfill only after stable schema; never replay authority | No shared canonical project schema; transcript format is unstable |

## Proven Gaps

Before this task there was no tracked implementation that could:

1. reproduce a repository-wide node/edge graph from an exact Git revision;
2. connect source symbols to imports, statically resolved calls, tests, BAT/CLI
   entrypoints, DB schemas, runtime ownership, policies, or external boundaries;
3. map project nodes bidirectionally to bounded dialogue contours;
4. detect a stale SESSION/TASK commit identity before loading it;
5. keep hypotheses distinct from evidence-backed root causes;
6. maintain decision supersession and causal-chain links;
7. build a small, authority-aware Context Packet under a token budget;
8. checkpoint verified deltas before compaction and restore a short manifest
   without treating the transcript as storage;
9. prevent the security repository, private runtime rows, Telegram identities,
   or model prompts from leaking into an unrelated context packet;
10. evaluate routing in shadow mode against explicit golden queries.

## Storage Decision

The existing public artifact guard rejects tracked SQLite and JSONL files. The
project brain therefore uses these non-competing layers:

- one revision-bound JSON graph is the canonical snapshot;
- Markdown and Mermaid are reproducible projections;
- an append-only JSONL event stream is created only under an explicit private
  local root;
- SQLite is a rebuildable private index over that graph and event stream;
- embeddings may find candidates in a future extension but never become truth.

This preserves the existing public/private boundary and avoids turning the
candidate Obsidian graph, outcome memory, SESSION, or a vector database into a
second authority.

## Initial Inventory Metrics And Supersession

The Phase 1 build over the baseline commit found 10,440 nodes and 19,895 edges:
811 parsed Python modules, 70 BAT entrypoints, 225 Python CLIs, 27 strategy
families, 9 declared database authorities, and 57 statically declared tables.
The initial report assigned every source module a status and owner label, but
its scalar `100%` measures were not honest semantic-coverage claims: one metric
divided the module count by itself, document coverage was constant, fallback
`repository_maintainers` labels hid ownership uncertainty, and dialogue
containment made the orphan count look like zero. Those scalar metrics are
superseded by structured numerator/denominator/method records for syntactic
parsing, semantic catalog coverage, test links, active-document cataloguing,
verified/rule/fallback ownership, unresolved calls, semantic duplicate
candidates, and meaningful non-containment connectivity. No generated report
may call the result a complete or 100% full project map.

## Baseline Regression Exception

The full Windows non-live suite reached `2774 passed, 2 skipped` and one
pre-existing failure in
`test_production_run_sweep_handles_real_sqlite_writer_contention_fail_closed[True]`.
The exact node failed with the same `StaleTaskClaimError` in this task worktree
and in unchanged stable main; no project-brain module is in that production call
chain. This PR must not hide or repair that unrelated claim/progress timing
defect. It remains a separate gate even if Linux CI does not reproduce the
Windows scheduler/filesystem behavior.
