# Roadmap

Status: **CURRENT**

- Verified: 2026-08-01
- Verified against: `7831592cda5e204db2de37da4404f0ee7aac64cd`
- Scope: completed, current, next, and later evidence gates
- Evidence: [Trading Portfolio Roadmap](docs/trading-portfolio-roadmap.md) and
  current GitHub issue state
- Residual risks: issue priority and private operational state can change after
  this verification point.
- Next gate: refresh this sequence whenever a module status or owner changes.

The project is a paper/research workbench. This sequence is ordered by evidence
dependency, not by a route to live trading.

## Completed

- Public market/news ingestion, bounded experiment records, deterministic
  simulation contracts, independent validation bridge, fenced candidate
  lifecycle, paper observation, advisory model boundaries, and guarded paper
  delivery all have production code and non-live tests.
- Process ownership, monotonic fencing, renewable task/job claims, idempotent
  materialization, and coordinated fail-closed RCC shutdown have public test
  contracts. The listener safety probe uses isolated native Windows TCP
  inventory while retaining bounded Job Object cleanup and fail-closed
  freshness enforcement.
- Public storage foundations include bounded synthetic migration, integrity,
  reachability, rollback, and plan-digest-bound backup retention proofs. The
  latter preserves one full unpacked generation and restore-verifies
  content-addressed older evidence before exact-file reclamation. None of
  these contracts activates a private root without owner authority.
- Repository quality gates cover the full non-live test suite, Python quality,
  supply-chain policy, tracked-artifact policy, documentation links, and public
  entrypoint inventory.

Completed means the bounded public contract exists. It does not mean the
private runtime, data, or trading hypothesis is proven.

## Current: Storage Containment Before Reliability

1. Catalog and hash every quiescent operational backup generation.
2. Bind the retained unpacked generation to separate integrity/restore evidence.
3. Archive older baseline, incident, and canary evidence into deduplicated,
   content-addressed objects before exact-file reclamation.
4. Prove interruption recovery, archive restoration, plan-digest enforcement,
   and zero-change second apply.
5. Fail closed before canary when backup size or free space exceeds the
   reviewed budget.

Exit gate: exact-head CI and post-merge checks green, private cleanup matches
one dry-run plan digest, archive verification is green, second apply changes
zero files, and the backup/free-space status is within budget.

## Next: Continuous Paper-Only Reliability

1. Enforce the reviewed backup-size and free-space budget; cleanup must use a
   hashed dry-run plan and a separately authorized exact apply.
2. Start only the canonical RCC paper profile under a fresh operational
   authority and quiescent preflight.
3. Establish T+0 only after mandatory readiness, one process authority,
   fencing, integrity, and execution-denial checks pass.
4. Observe lifecycle, claims, data lineage, delivery, storage growth, and real
   progress for the full bounded window.
5. Stop gracefully at duration or at a proven hard fail; do not repair or
   restart during the canary.

Exit gate: a green long-duration report with processes/owners/ports at zero
after stop and canonical DB integrity confirmed. This is operational evidence,
not a profitability result. GitHub issue #224 tracks this gate.

## Then: Data, Role, And Signal Quality

- Measure missing-data and interruption behavior across ingestion, farm,
  validation, paper outcome, analyst, and delivery boundaries.
- Calibrate trader, validator, and analyst roles against immutable held-out
  cases; record what each role knew before the proposed entry point.
- Version what-if entry, exit, and risk variants instead of overwriting the
  original decision history.
- Evaluate LLM advisory usefulness only after deterministic evidence storage,
  invocation traceability, and lifecycle continuity are reliable.
- Revisit policy calibration tracked by issue #155 only with preregistered
  evidence and an untouched evaluation set.

## Later And Separately Gated

| Initiative | Missing proof | Return gate |
|---|---|---|
| Canonical private archive/storage adoption | Producer/reader inventory, quiescent parity, capacity, cutover, abort, and rollback evidence | Separate migration authority and reviewed operational package |
| Broader strategy/search families | Reliable data and untouched comparison protocol | Predeclared trial identity and independent validation |
| Model fine-tuning or adapter training | Stable prompt/tool/RAG baseline and enough immutable labeled evidence | Held-out evaluation shows a repeatable residual gap |
| External simulator integration | Semantic parity, licensing, and isolation | Deterministic comparison against the local truth-tier contract |
| Any execution product | Separate architecture, risk model, account boundary, and owner decision | Outside this roadmap until explicitly authorized |

Historical designs remain evidence only. The superseded adaptive-paper decision
is retained in [docs/deferred-adaptive-paper-architecture.md](docs/deferred-adaptive-paper-architecture.md),
while current capability and missing proof live in the machine roadmap.

## Permanent Non-Claims

- No current phase proves profitability or live readiness.
- A model, validator, paper result, CI run, or canary cannot grant execution
  authority.
- Private research and operational evidence stay outside public Git.
