# Roadmap

Status: **CURRENT**

- Verified: 2026-08-12
- Verified against: `5a397edfb2787f51fdac12ef5f983a894b78a2a2`
- Scope: completed, current, next, and later evidence gates
- Evidence: [Trading Portfolio Roadmap](docs/trading-portfolio-roadmap.md) and
  current GitHub issue state
- Residual risks: issue priority and private operational state can change after
  this verification point.
- Next gate: refresh this sequence whenever a module status or owner changes.

The project is a paper/research workbench. This sequence is ordered by evidence
dependency, not by a route to live trading.

## In Review On The Task Branch

The unmerged `codex/stale-materialization-recovery` branch is a narrow recovery
candidate, not an authoritative operational capability. It must prove that an
expired run-sweep whose exact compute materialization is already durable can be
adopted without requeue, fence advance, or a second queue/outbox/materialization
effect. Its hash-bound plan must fail closed on task, outbox, digest, status or
queue-binding drift, and a second exact apply must change zero rows.

Exit gate: focused crash/fence/replay tests, lifecycle and integration regression,
full non-live tests, repository guards, clean exact-head review, synchronized
documentation, and a separate merge decision. Applying the disposition and
starting runtime remain later operational gates.

## Completed

- PR #277 integrated bounded scanner intake fairness, validation freshness and
  stable-generation publication, exact paper identity, pre-delivery advisory
  fallback, truthful product monitoring, bounded rebuildable snapshots and a
  deterministic production-chain E2E/replay proof.
- Public market/news ingestion, bounded experiment records, deterministic
  simulation contracts, independent validation bridge, fenced candidate
  lifecycle, paper observation, advisory model boundaries, and guarded paper
  delivery all have production code and non-live tests.
- Process ownership, monotonic fencing, renewable task/job claims, idempotent
  materialization, and coordinated fail-closed RCC shutdown have public test
  contracts. Exact owned-process-handle liveness, authority/fencing SQLite,
  filesystem/Telegram status, native Windows TCP inventory, product progress,
  and deep database health use independent supervised freshness lanes. Slow
  I/O cannot starve the 15-second process lane, and every blocked lane reports
  its exact active stage. Listener Job Object cleanup uses kill-on-close rather
  than a synchronous termination call; repeated inventory loss and complete
  listener identity violations remain fail-closed.
- An expired owner whose persisted process start predates the current Windows
  boot can be atomically reclaimed even when its reused PID is not probeable.
  Same-boot ambiguity and unexpired authority still fail closed, and takeover
  advances rather than resets the fence.
- Validation maintenance continuously drains from the priority worker using the
  configured batch capacity, publishes durable progress only after completed
  export/check/artifact chunks, and rechecks owner/fence authority before each
  side effect. Bounded fair selection passes stale orphan, ineligible, and
  artifact-unavailable head tasks instead of letting one row consume every
  service slot. Classification yields at a configurable validation high-water
  mark without dropping its queued work. Aggregate backlog, oldest age,
  arrival/service rates, and drain estimates expose the service SLO; a finite
  retry budget terminally classifies repeated no-artifact/no-verdict work. A
  batch with no exportable candidate preserves an existing current-code
  generation. If that authority is `code_stale`, maintenance publishes one
  idempotent current-code `ready_empty` generation after its active/fence check;
  pending, invalid, unavailable, and ambiguous states remain fail-closed. This
  path does not rescan the full historical validation corpus.
- A completed priority-worker validation generation now raises a one-shot
  product-cycle wakeup. The foreground farm loop consumes it before its normal
  180-second cadence, then immediately re-verifies and propagates the new
  generation. Stop intents remain higher priority and the bounded startup
  deadline is not extended.
- Paper runtime consumers share one content-verified current-generation
  snapshot per cycle, load only manifest-named active cards, and expose explicit
  pending/stale/empty/invalid availability states. Signal evaluation retains the
  prefix no-lookahead contract inside a bounded recent horizon derived from the
  strategy history requirement and paper hold window; completed chunks advance
  process-lease liveness and recheck fail-closed cancellation.
- Paper market-data observation now distinguishes provider failure, candle
  continuity gaps, and a successful empty provider response. These conditions
  preserve active observations and produce deduplicated operational
  incident/recovery evidence; defense-in-depth selectors exclude them from
  family ranking, training projections, setup memory, adaptive recommendations,
  and advisory outcome-review inputs.
- Paper Evidence v2 is wired into the canonical farm as a required,
  operator-activated generation authority. One outer farm identity owns the
  co-located renewable paper fence; bridge, consumer, queue, observation,
  account, and projection promote atomically. Current-generation readers,
  training, lineage, calibration, memory, quality, and validated Telegram delivery bind
  to the resulting run. Producer membership is restricted to validation-bound
  `pfr_farm` signals. Broad `farm` observations remain outside generation,
  account, lifecycle, and training authority even if they carry forged
  validation-like fields; the existing preview may deliver them only as labelled
  `farm_calculated` research cards under an independently rechecked content-hash
  envelope whose authority is `none`. A malformed PFR identity, changed research
  snapshot, missing cutover, stale fence, stage failure, or preview mismatch fails
  closed. Public code provides shadow-parity,
  activation, status, and non-destructive pre-runtime rollback primitives.
- Current-generation preview, training, lineage, outcome retest and Telegram
  delivery form the generation boundary before analyst, role and setup-memory
  maintenance. An intermediate delivery checkpoint remains observable, but T+0
  now waits for the final product-cycle checkpoint containing safe delivery,
  advisory, analyst, memory and storage-maintenance aggregates.
- The post-delivery analyst hot path skips empty outcome/draft sources, routes
  only bounded current-generation feedback, and hands exact environment IDs to
  role dispatch and reconciliation. Historical role-environment directories
  remain immutable evidence rather than a startup work queue. Stage milestones
  and stop/claim checks make this maintenance interruptible without publishing a
  premature farm-ready checkpoint.
- Setup-outcome memory no longer rereads every historical run artifact in each
  product cycle. Its derived cache binds the deterministic reject-taxonomy
  version and context, every candidate source digest, and the bounded
  run-artifact stat identity. A previous complete snapshot bootstraps only
  unchanged pre-snapshot rows; changed groups use the ordinary classifier and
  atomic cache publication. Cache hits, bootstrap hits, invalidations,
  recomputations, rereads, and unavailable artifacts remain visible in product
  progress, while current-generation training selection stays authoritative.
- Telegram transport success is not revoked by a later runtime-log sink error;
  the acknowledged message id reaches the outbox. A new current-attempt ambiguity
  fails closed, while carried ambiguous debt stays visible and blocked from
  automatic replay. Chart cards use one photo-caption request, so a subscriber
  cannot receive a confirmed photo followed by a missing analysis text. A
  connection-establishment failure is classified as not attempted and may be
  retried by a later cycle; post-connect unknown outcomes remain ambiguous. Card
  analysis provenance distinguishes accepted bounded LLM
  advisory evidence from deterministic fallback and authority-none research
  templates without granting the model price or lifecycle authority.
- Scanner passes and completed farm cycles publish atomic, secret-free product
  checkpoints. The RCC supervises them on an independent lane: ordinary PIDs
  and heartbeats cannot establish T+0 or refresh a product SLO. Honest zero
  output is `idle`, public-provider loss is `degraded`, and stale stages,
  validation oldest-age breach, cross-generation races, or retained technical
  learning rows fail closed. The green T+0 report transports the immutable
  launch-time product boundary into the steady monitor and re-verifies current
  component sequences, so transition between monitor adapters cannot reclassify
  the same accepted startup checkpoint as pre-run; genuine post-T+0 staleness
  retains the existing fail-closed SLO. A newer monotonic generation may enter
  the shared bounded `product_transitioning` state between the T+0 write and
  external monitor initialization without invalidating that handoff. Regression,
  inconsistent state and every unbounded non-ready report remain fail-closed. A
  normal fail-closed validation publication may temporarily move product state
  to `product_transitioning` after T+0; it does not reuse the startup timeout
  while fenced worker and product progress remain live.
- A long first farm cycle may use fresh canonical completed-chunk milestones to
  remain in startup after the ordinary 300-second completion SLO, but the cold
  start still fails closed at 600 seconds. Missing stage/milestone identity,
  stale progress, or heartbeat-only activity never extends the budget.
- T+0 binds to the mandatory generation, delivery, analyst, setup-memory and
  quality boundary. Optional calculator and broad role-review LLM maintenance
  continues under the steady-state SLO and cannot block initial readiness.
  RCC and external canary adapters consume the same classifier: only an explicit
  current-run validation wait without hard-fail reasons is transitional, while
  every other non-ready report remains fail-closed.
- Product checkpoints distinguish validated PFR setups, authority-none research
  observation cards and actual analyst inputs. Ten rendered research images with
  no current validated setup are therefore reported as degraded research output,
  not as ten validated signals or ten learned outcomes.
- Canonical buffered scanner passes now have separate total-pass and article-
  resolution wall budgets. Network extraction receives the remaining bounded
  timeout, the unbounded archive fallback is excluded from this canonical mode,
  and only completed source, document, normalization, or card chunks publish
  progress. Deferred rows remain durable and visible as degraded backlog; they
  do not hold RCC startup indefinitely or manufacture an empty success.
- RCC startup now publishes a digest-bound attempt identity and completed stage
  before the UI heartbeat exists. Constructor failures retain only their safe
  exception type, BAT wrappers preserve the real exit code, and the startup
  adapter rejects a dead or PID-reused process generation instead of waiting
  through the full cold-start budget.
- RCC readiness now requires an identity-matched, fresh successful Telegram
  `getUpdates` poll. A live bot PID without a working poll remains
  `provider_waiting` during the bounded startup budget and fails closed at the
  deadline; a stale poll after T+0 is a hard failure.
- Public storage foundations include bounded synthetic migration, integrity,
  reachability, rollback, and plan-digest-bound backup retention proofs. The
  latter preserves one full unpacked generation and restore-verifies
  content-addressed older evidence before exact-file reclamation. None of
  these contracts activates a private root without owner authority.
- Runtime append surfaces now have a separate, off-by-default exact-root
  rotation capability. Writer-coordinated atomic sealing replaces unsafe
  copy/truncate; immutable content-addressed archive objects are restore-checked
  before source release, interrupted work remains recoverable, recent tails and
  semantic dedup state stay bounded/available, and storage-budget failure is a
  canonical farm hard fail after activation. The configured lock wait is a
  shared monotonic budget for local-thread and OS-level interprocess contention;
  transient concurrent contour writes retry, while a holder that outlives the
  budget remains fail-closed. This is implementation proof only; private
  activation endurance remains in the operational gate.
- Repository quality gates cover the full non-live test suite, Python quality,
  supply-chain policy, tracked-artifact policy, documentation links, and public
  entrypoint inventory.

Completed means the bounded public contract exists. It does not mean the
private runtime, data, or trading hypothesis is proven.

## Current: Product-Chain Integrity Before Reliability

1. Prove validation service capacity, artifact-aware fairness, high-water
   backpressure, fresh/FIFO service balance, and oldest-age SLO observability
   under synthetic contention. The task-branch implementation candidate must be
   merged and independently verified before this is treated as current behavior.
2. Verify during the bounded canary that the implemented provider-error/data-gap/
   genuine-no-market-data taxonomy preserves observations through a real outage
   and recovery while technical evidence remains absent from all learning inputs.
3. Apply the now-implemented Paper Evidence v2 private cutover only through the
   separately authorized quiescent backup/restore, shadow-parity, integrity,
   and exact-revision operational gate.
4. Verify the implemented end-to-end product-progress and generation-freshness
   monitor under the private canary; PID liveness alone is not readiness.
5. Apply the implemented bounded private storage rotation only after exact
   backup, restore, digest, interruption, semantic-index parity, and idempotent
   cleanup proofs; verify concurrent contour writers over the full canary.

Exit gate: each phase has an exact-head green scoped PR and green post-merge
verification; operational cutover/cleanup then passes backup, restore, parity,
digest, and zero-change second-apply gates before runtime.

## Next: Continuous Paper-Only Reliability

1. Enforce the reviewed backup-size and free-space budget; cleanup must use a
   hashed dry-run plan and a separately authorized exact apply.
2. Start only the canonical RCC paper profile under a fresh operational
   authority and quiescent preflight.
3. Establish T+0 only after mandatory readiness, one process authority,
   fencing, integrity, and execution-denial checks pass.
4. Observe lifecycle, claims, data lineage, delivery, storage growth, and real
   product progress for the full bounded window, including validation arrival
   versus service rate, oldest backlog age, generation freshness, and the
   absence of technical outcomes in learning inputs.
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
