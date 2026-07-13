# Adaptive Research Center Contract

Status: implementation contract for issue #172. Updated 2026-07-13.

This document defines the paper-only adaptive loop shared by the calculation
farm, independent validator, Trader Supervisor, and System Analyst. It does not
authorize live trading and does not make an LLM an execution or validation
authority.

## Design Rule

The deterministic core owns numbers, evidence, validation verdicts, lifecycle
state, and all safety boundaries. LLM roles may classify context, propose a
bounded hypothesis, criticize it, or explain evidence. A schema-valid LLM
answer is a draft, not accepted evidence.

```text
public market observations
          |
          v
typed parameter space -> farm search manifest -> candidate evidence
          ^                                      |
          |                                      v
role feedback inbox <- System Analyst <- paper outcomes <- Trader Supervisor
          |                    |                         ^
          |                    v                         |
          +------------> independent validator ---------+
```

Every arrow is a versioned contract. No component mutates another component's
state directly.

## Role Ownership

### Calculation Farm

Owns reproducible, budgeted exploration of an explicitly typed parameter
space. A family parameter declares its type, unit, domain, bounds, dependencies,
and whether it is searchable. The farm records tested and omitted points and
uses a deterministic space-filling selection when the Cartesian product is
larger than the resource budget.

The local calculator mini-swarm may select a hypothesis or search dimension and
explain the choice. It cannot provide prices, numerical trade levels, a
validator verdict, or execution authority. Deterministic code materializes the
actual grid.

### Independent Validator

Owns evidence quality and the only `PAPER_FORWARD_READY` verdict. It receives an
immutable validation envelope with data and simulator provenance, applies costs
exactly once, rejects unsupported contract versions, and keeps selection data
separate from evaluation data.

The validator may accept a System Analyst request to run an additional test.
It cannot accept a request to change a verdict. Policy changes remain explicit
code/config changes with their own evidence and review.

Contract `CandidateForValidation 1.1` requires a `ValidationEpoch.v1` whose
evaluation fingerprint differs from the selection fingerprint and whose first
observation is later than the frozen hypothesis. A legacy or same-window farm
series receives `NEEDS_MORE_DATA`, never paper-forward readiness.

### Trader Supervisor

Owns a deterministic per-symbol paper lifecycle. It consumes aligned market
snapshots and optional visual-evidence references, then reduces ordered events
into explicit states. Duplicate, stale, contradictory, degraded-data, and
reversal events must be replayable.

A multimodal model may describe chart evidence or advise on a bounded label.
It cannot transition lifecycle state, change levels, open or close an order, or
access a private image unless an explicit sanitized adapter permits it.

### System Analyst

Owns post-outcome diagnosis and feedback provenance. It can address farm,
validator, or trader independently. Each feedback item includes the frozen
hypothesis time, knowledge cutoff, source snapshot, evidence references,
uncertainty, required gate, prohibited actions, and recipient acknowledgement.

The analyst does not promote its own counterfactual. A result selected from a
grid on one data window is only a hypothesis for a later untouched shadow or
forward window.

The canonical cycle acknowledges only that a recipient accepted a bounded
research request. That acknowledgement is not policy application. A separate
recipient gate may mark an environment accepted only after it cites the
deterministic gate result and an untouched evaluation artifact.

Accepted requests are dispatched to existing deterministic owners rather than
parallel engines: farm retests use `schedule_retest`, untouched validation uses
`export_validation`, and trader work is a paper-only FSM replay. Terminal work
is normalized into `SystemAnalystResultInput.v1`; each result is reviewed once.
Follow-up generation two is terminal and cannot recursively create more work.

## Role Environment Versions

"Learning" in this workbench means producing a new, inspectable environment
version for a role:

- farm: parameter-space/search-policy version;
- validator: test-request or policy-candidate version;
- trader: lifecycle/advisory-policy candidate version;
- analyst: diagnosis/prompt/evidence-policy version, changed through reviewed
  code/config rather than feedback addressed to itself.

An environment candidate version is immutable. Recipient acceptance and policy
gate state are stored separately and ledger acknowledgements are the source of
truth; acknowledgements bind artifact paths and content hashes, and a state
projection is written only after its matching ledger event. The
combined view records its parent, accepted feedback IDs, deterministic gate
result, evidence window, and status. It is never a
silent prompt rewrite and never a model-weight update. Weight tuning is a
separate future research track requiring a dataset and evaluation protocol.
System Analyst feedback recipients are deliberately limited to farm, validator,
and trader; the analyst cannot approve its own environment.

## Temporal And Evaluation Boundary

Every adaptive artifact must distinguish:

- `observed_at`: when the market fact existed;
- `available_at`: when the system could legally know it;
- `hypothesis_frozen_at`: when the hypothesis stopped changing;
- `knowledge_cutoff_at`: latest fact available to the analyst;
- `evaluation_started_at`: first untouched evaluation observation;
- `outcome_window_end`: end of the realized outcome window.

Evaluation data must begin after the hypothesis was frozen. Once a holdout has
been used for feedback, it becomes training history and cannot be called an
untouched holdout again.

## Public And Private Storage

Public Git contains contracts, deterministic code, tests, synthetic fixtures,
and conservative documentation. The private research root contains candles,
account state, raw prompts/responses, chart files, journals, candidate rankings,
feedback ledgers, acknowledgements, and research results. Public tests must use
synthetic data and temporary directories.

The private feedback ledger uses serialized writers, content-bound source
snapshots, and a hash chain to detect internal event mutation or a chain break.
Deletion of a complete valid trailing suffix is not detectable without an
external anchored head, so filesystem durability and backup remain required.
It is not a cryptographic defense against an attacker who already has write
access to the same operating-system account and can rewrite both the ledger and
its chain. Filesystem permissions and workstation security remain the trust
boundary; custom hidden cryptography is deliberately not introduced.

## Safety Invariants

1. `paper_only=true` and `execution_allowed=false` remain fail-closed.
2. No LLM output may set side, entry, stop, take-profit, leverage, order fields,
   validator verdict, or lifecycle state.
3. No adaptive artifact may edit `.env`, credentials, process controls, or live
   exchange configuration.
4. Unsupported schema versions, missing provenance, stale snapshots, and
   invalid temporal order are explicit failures, not best-effort warnings.
5. Every routed request has a recipient acknowledgement and request artifact.
   Policy application additionally requires a later gate result and untouched
   evaluation reference; otherwise the environment remains unapplied.

## Verification Gate

Issue #172 is complete only after:

1. targeted tests for all four roles pass;
2. integration tests replay feedback from outcome to each bounded recipient;
3. leakage and no-authority tests pass;
4. private/public scans find no private artifacts or credentials;
5. independent correctness, architecture, security, and documentation reviews
   report no unresolved blocking findings;
6. GitHub required checks pass and the reviewed pull request is merged;
7. no runtime process is launched as part of completion.
