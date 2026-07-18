# Documentation Guide

Status: **ACTIVE**. This is the navigation entry point for documentation in
`trading-bot-v2`.

## How To Read This Repository

The public repository documents a paper/research workbench. It does not contain
private strategy research, candidate rankings, raw runtime state, credentials,
or a live-trading product.

Read in this order:

1. [README](../README.md) for scope, supported public setup, and claim limits.
2. [Architecture](../ARCHITECTURE.md) for system boundaries and ownership.
3. [Current State](../CURRENT_STATE.md) for the active engineering state.
4. [Roadmap](../ROADMAP.md) for current work and deferred initiatives.
5. [Farm Ownership Map](farm_ownership_map.md) before starting any runtime.
6. [Entrypoint Catalog](entrypoints.md) before choosing a Windows launcher.
7. [Farm Runbook](farm_runbook.md) for supported paper/research operations.
8. [Project Map](project-map.md) for module and repository ownership.

## Documentation Authority

| Level | Document | Owns |
|---|---|---|
| Public front door | `README.md` | Scope, safe first run, non-claims, license. |
| Current architecture | `ARCHITECTURE.md` | Active components, boundaries, ownership. |
| Current engineering state | `CURRENT_STATE.md` | What is active now; not a historical diary. |
| Development sequence | `ROADMAP.md` | Current phases, blockers, deferred work. |
| Operator instructions | `farm_runbook.md` | Preflight, start, stop, health checks. |
| Storage policy | `storage_boundaries.md` | What may be public, private, or only local. |

When documents disagree, follow this table. Dated reports, prior plans, and
session handoffs are local history; they do not override the current
architecture.

## Active System Documents

### Farm And Paper Lifecycle

- [Project map](project-map.md): ownership of code directories and boundaries.
- [Document catalog](document-catalog.md): current, reference, archive, and
  local-only classification.
- [Entrypoint catalog](entrypoints.md): owner, status, and side effects for all
  Windows batch commands.
- [Farm ownership map](farm_ownership_map.md): active, diagnostic, and legacy
  launch-path ownership.
- [Farm loop lifecycle](farm_loop_lifecycle.md): current calculation lifecycle.
- [Farm runbook](farm_runbook.md): supported paper/research operation.
- [Paper runtime design](paper_runtime_design.md): validated setup observation
  and outcome recording.
- [Paper evidence generations](paper-evidence-generations.md): immutable v2
  event/account authority, current projections, legacy display, and rollout
  boundary.
- [Paper acceptance cycle](paper_acceptance_cycle_2026-07-10.md): acceptance
  criteria for a bounded paper run.
- [Outcome learning loop](outcome_learning_loop_2026-07-05.md): paper outcome
  review, bounded retests, and evidence gates.
- [Adaptive Research Center Contract](adaptive-research-center-contract.md):
  active farm, validator, Trader Supervisor, and System Analyst contracts.

### LLM And Notification Boundaries

- [Local calculator mini-swarm](local_calculator_swarm_2026-07-10.md): local
  advisory roles and the invocation ledger.
- [LLM proposal contract](llm_proposal_contract.md): bounded advisory output.
- [Farm notification layer](farm_notification_layer.md): separate delivery edge
  for user-facing paper cards.
- [Public channel news flow](public_channel_news_flow.md): public news-channel
  content flow; it is not a trading instruction path.

### Scanner And Reference Surfaces

- [Scanner specification](../SCANNER_SPEC.md): upstream information intake.
- [Scanner-to-TA contract](scanner_ta_confirmation_contract.md): paper-only
  technical confirmation boundary.
- [Main research verdict index](main_research_verdict_index.md): why old Main/TA
  remains a confirmation/reference surface.

### Public/Private Policy

- [Validation bridge contract](validation-bridge-contract.md): public-safe
  candidate, verdict, and authority boundary with `honest-backtest`.
- [Simulator truth tiers](simulator-truth-tiers.md): immutable OHLC fixture/scenario
  assumptions, metric states, unsupported execution dimensions, and claim ceilings.
- [Storage boundaries](storage_boundaries.md): public/private/local-only artifacts,
  report-only automatic maintenance, and synthetic-only quarantine limits.
- [Public artifact policy](public-artifact-policy.md): staging and historical
  artifact-remediation rules.
- [Remote data manifest](REMOTE_DATA_MANIFEST.md): machine-local data locations
  and handling rules.
- [Deferred adaptive paper architecture](deferred-adaptive-paper-architecture.md):
  historical decision record superseded by the active contract.

## Status Labels

| Label | Meaning |
|---|---|
| **ACTIVE** | Current source of truth for the named responsibility. |
| **REFERENCE** | Useful stable explanation; defer to current architecture for status. |
| **DECISION** | Dated decision record; not current authority by itself. |
| **ARCHIVE** | Historical research or operator context. Do not use it to start a runtime. |
| **LOCAL ONLY** | Exists on a workstation but must not be committed or linked as public evidence. |

## Historical Material

Older research, postmortems, prompts, and runtime handoffs are preserved only
in local/private history. They are intentionally excluded from this public
repository. A current document must state the method or conclusion again if it
is needed for public operation.

A deliberately limited exception is [Legacy Evidence](legacy-evidence/README.md):
sanitized retrospective narratives that explain closed hypotheses and historical
limitations without publishing raw research artifacts or current strategy data.

## Related Public Repository

[honest-backtest](https://github.com/krivonosoff161/honest-backtest) owns the
independent validation methods. This repository owns the paper/research
workbench and passes candidates through a bounded validation bridge. Neither
repository grants live order authority.
