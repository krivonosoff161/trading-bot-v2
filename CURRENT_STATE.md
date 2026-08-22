# Current State

Status: **CURRENT**

- Verified: 2026-08-22
- Verified against: `3c8499b2142b6884844b65c348d1c3571f74d68e`
- Scope: implemented public capabilities, bounded limitations, and next gates
- Evidence: [machine roadmap](docs/trading-portfolio-roadmap.yaml) and the
  production tests linked for each module
- Residual risks: private runtime health, data completeness, and service
  availability are deliberately excluded.
- Next gate: complete a separately owner-authorized long paper-only canary
  before claiming continuous operational reliability.

This page describes the public repository, not a live runtime. It does not
publish process identities, private database rows, observations, outcomes,
model conversations, recipients, credentials, or workstation-specific state.

## Integrated Product Liveness Baseline

PR #277 is integrated at `dc4d6db71fc12abde330b10d9a78258e2b0084e6`.
It closes the bounded product-chain gaps below without broadening paper-only
authority:

- Scanner intake filters caller-supplied, already-ingested event identities
  before applying its bound. It selects by canonical priority and freshness, so
  a long prefix of still-open historical watches cannot hide a fresh event.
- Validation uses a durable fairness lane: one bounded recent-task opportunity
  is interleaved with FIFO debt service. The same transaction selects the task
  and advances the fairness cursor, preserving old-work progress while bounding
  fresh-task starvation.
- A successor validation build has a separate staging manifest. The last
  completed current generation remains consumable until its successor is
  atomically complete; staging never silently revokes current authority, and a
  first-generation pending state remains fail-closed.
- Paper outcome-memory suppression uses the exact symbol, timeframe, family,
  and parameter identity. Missing identity fields are never wildcards, and a
  new unrelated validation batch does not invalidate an active setup whose
  exact authority remains current.
- The bounded calculator-advisor opportunity moves before preview and delivery
  for current-generation feature packets. A missing, rejected, timed-out, or
  exhausted advisory remains an explicitly labelled deterministic fallback;
  model output still cannot change prices, lifecycle, or authority.
- Product progress reports scanner-intake age, validation backlog age and rates,
  successor-build age, and generation transition age using truthful timestamps.
  A stable current generation cannot conceal a stalled successor build.
- Rebuildable derived snapshots use a byte budget, content digest, atomic
  replace, and no-op identical writes instead of unbounded append semantics.
  Deterministic synthetic E2E coverage must prove one causally linked product
  flow and replay idempotency.

Expired materialization adoption is integrated at
`e03502ddd484234c64ccb1d51b56c8397789b55f`. It remains an exact-task,
hash-bound operational mechanism and grants no general reconciliation authority.

## Integrated Cold Setup-Memory Startup Boundary

PR #283 is integrated at `ce592269dc4f4d4c1360f0fca68c35de2eaf55b3`. It
preserves current generation, complete known-bad authority, analyst, role,
calibration and quality gates before T+0, while moving only derived historical
setup-memory refresh to a typed, bounded, resumable backfill after the completed
product checkpoint. Its partial cache is an accelerator, never complete memory
authority. Missing, corrupt, incomplete or digest-mismatched known-bad evidence
still blocks the canonical Paper Evidence v2 lane fail-closed. The integrated
crash-safe evidence-seal library is not yet an external canary finalizer
activation. Operational Paper Evidence v2 rebind and a paper-only canary remain
separate owner gates.

## Integrated Windows Lease-Supervisor Causal Verification

PR #284 is integrated at `463dc28e749e0967380a71dbb351b2548d01ae35`. It
corrects a timing-sensitive synthetic test without altering production
owner/fence/heartbeat code or its 90-second production lease. The test now
establishes one successful renewal before checking additional renewals while
the parent blocks the GIL. A persistent synthetic SQLite writer block still
must fail closed before lease expiry, create the canonical stop request, and
publish the bounded renewal-budget failure.

## Integrated Stop-Marker Provenance Boundary

PR #285 is integrated at `188a0c114979742abf61a81181c8dde97c14370a`. The
ordinary clear command continues to reject unknown marker provenance. A separate
typed migration accepts only an externally authorized marker pair bound by exact
name, SHA-256 and byte length, archives opaque bytes privately before removal,
and is idempotent under replay. It does not accept arbitrary marker text,
encodings, names, roots, or hash drift. Runtime rebind and launch remain
separate operational gates.

## Integrated Startup-Liveness DAG Boundary

PR #286 is integrated at `3c8499b2142b6884844b65c348d1c3571f74d68e`. It
preserves the 600-second ceiling and all current-generation, known-bad, Paper
Evidence V2, owner/fence/stop, stale-generation and delivery gates while moving
only non-authoritative analytical and legacy research work behind the immutable
V2 delivery checkpoint. It proved that an already-observed
`waiting_validation_generation` pass cannot fabricate readiness. Operational
proof remains a separate canary.

## Integrated T+0 Handshake And Post-Stop Integrity

PR #287 is integrated at `0b4263ffa7e3c910399406f8502dfad38c4b6661`. It
preserves a validation-publication wake until an exact-current bounded V2
re-entry acknowledges it, so the next product pass cannot silently fall back to
intake, discovery, historical backlog or broad research before delivery. The
re-entry still requires known-bad authority, exact Paper Evidence V2 generation,
guarded Telegram delivery and owner/fence/stop checks before the only mandatory
farm checkpoint. The canonical post-stop target resolver derives candle and
Paper Evidence targets from the active manifest/root instead of guessing
obsolete paths. A fresh post-merge and operational canary proof remains
required; this makes no profitability or live-readiness claim.

## Task-Branch Candidate: Lease-Supervisor Failure Publication

The candidate branch `codex/lease-supervisor-publication-repair` is based on
the merged PR #287 baseline. It records the child-side failure-detection and
durable stop-intent commit times, publishes the supervisor failure event after
the durable stop/status boundary but before best-effort alert append I/O, and
tests that causal boundary without using parent bridge scheduling as a lease
deadline. Owner identity, fencing, renewal failure budgets, canonical stop
intent and execution denial are unchanged. The candidate is non-authoritative
until exact-head review, merge, post-merge checks and the separately authorized
paper-only canary.

## Implemented Public Contracts

| Capability | Status | Evidence ceiling |
|---|---|---|
| Public research ingestion | implemented bounded | Adapters and degraded modes are tested. Canonical scanner passes bound article resolution by both per-fetch and total wall budgets, publish progress only after completed documents, preserve deferred rows in the durable buffer, and expose bounded deferral as degraded rather than fabricating a signal. Continuous provider availability is not proved. |
| Strategy Lab and experiment registry | implemented bounded | Bounded scheduling, lineage, and candidate records exist; a long reliability window remains open. |
| Deterministic simulation | implemented bounded | Declared truth tiers and synthetic parity are covered; full market execution fidelity is not claimed. |
| Independent validation bridge | implemented bounded | `honest-backtest` can try to falsify a candidate; a pass means only not rejected. |
| Fenced candidate lifecycle | implemented bounded | Owner, fence, claim, generation, and idempotency contracts are tested. Continuous validation maintenance honors the configured batch capacity, scans a bounded window past terminal orphan/ineligible and artifact-unavailable head tasks, and applies upstream classification backpressure at a configurable high-water mark without discarding classify work. Aggregate backlog, oldest age, arrival, service, and drain estimates are observable; a finite retry budget terminalizes repeated failures. A batch with no exportable candidate preserves a current-code generation, but replaces a `code_stale` generation with one idempotent current-code `ready_empty` authority after rechecking active ownership; pending, invalid, unavailable, and ambiguous states remain fail-closed. Private runtime continuity and SLO compliance remain operational questions. |
| Paper observation | implemented bounded | Current-generation authority is content-verified once per cycle, active cards are loaded directly without a historical catalog scan, and signal evaluation preserves prefix no-lookahead semantics inside a declared-history-bounded recent horizon with completed-chunk progress and fail-closed cancellation. Public-data failures are classified as `provider_error`, `data_gap`, or `genuine_no_market_data`; they preserve the active observation, append separate operational incident/recovery evidence, and are censored from family ranking, paper/product training, setup memory, adaptive consumers, and LLM outcome review. The canonical farm requires an explicitly activated Paper Evidence v2 cutover, binds its writer lease to the same owner/process identity, atomically promotes bridge through projection, and blocks stale-generation Telegram delivery. Current-generation preview, training, lineage, outcome retest and actual Telegram delivery form the current product boundary. The startup-liveness candidate moves only their downstream analytical/research consumers behind that boundary; it does not relax complete known-bad authority, stale-generation delivery, owner/fence/stop checks, or cache completeness rules. Setup-outcome memory remains a typed historical backfill: only a complete identity-bound snapshot may be published, while a partial cache is resumable acceleration rather than memory authority. The complete v2 known-bad snapshot is digest-bound current-product authority: missing, corrupt, incomplete or mismatched snapshots block canonical v2 paper processing; a missing accelerator cache with a valid complete snapshot does not. Historical cache miss/corruption/schema mismatch recomputes only after the checkpoint in bounded slices, and stop, owner/fence, stale-generation and publication failures prevent publication. The cache never bypasses current-generation training selection or known-bad gating. Current lifecycle readers reuse one validated generation snapshot and do not fall back to historical validation cards while a current generation is pending or stale. Its producer generation accepts only validation-bound `pfr_farm` members. Broad `farm` observations cannot self-grant paper authority, enter the v2 bridge, account, lifecycle, or training surfaces; they may reach the existing Telegram preview only as explicitly labelled `farm_calculated` research cards under a separate content-hash freshness envelope with authority `none`. A changed source snapshot blocks delivery, malformed PFR identity still fails closed, and aggregate membership counts remain observable. Continuous private-runtime recovery remains unproved. |
| Research Control Center | implemented bounded | The canonical paper-only supervisor separates process/authority, listener ownership, database, real product-progress and Telegram poll-liveness lanes. A slow Windows TCP inventory cannot starve the 15-second process/owner/fence lane; its independent 90-second freshness contract still fails closed on repeated loss, while a complete foreign or missing Ollama listener remains an immediate hard failure. Listener timeout cleanup uses Job Object kill-on-close rather than synchronous termination behind a blocked kernel inventory call. Before its first heartbeat RCC publishes revision-bound, digest-verified startup stages; only sanitized exception types are retained, and a dead or PID-reused process fails startup immediately instead of consuming the full readiness budget. T+0 requires completed scanner and the final farm product-cycle checkpoint from the current run plus an identity-matched, fresh successful Telegram poll; an intermediate delivery checkpoint or live PID alone is insufficient. A first farm cycle that crosses the ordinary 300-second completion SLO may remain `starting` only while the canonical farm publishes fresh real stage/milestone completion evidence, and never beyond the bounded 600-second cold-start budget; missing, stale or timer-only progress still fails closed. The final checkpoint exposes delivery ambiguity, advisory/role degradation, analyst routing, setup-memory refresh, and storage-maintenance state as safe aggregates. Its accepted report carries the immutable launch-time product boundary into steady monitoring, which re-verifies component sequences instead of rebasing the run at T+0. If a newer monotonic generation enters the shared bounded `product_transitioning` state before the external monitor finishes initialization, the handoff remains valid; regressions, inconsistent reports and unbounded non-ready states still fail closed. After T+0 a content-safe `pending` validation publication remains observable while the fenced worker makes real progress; it does not re-enter the startup timeout. RCC and external canary adapters share one fail-closed post-T+0 classifier, so an arbitrary `ready=false` cannot be mistaken for that bounded transition. A new external ACK ambiguity, stale Telegram polling, stale stages, worker/claim failure, validation SLO breach, generation corruption, storage-maintenance failure, or technical learning leakage fails closed. Carried ambiguous ACK debt remains visible but is never retried automatically. A completed zero-signal pass is honest idle and bounded provider/advisory degradation remains observable. A fresh 48-hour canary is still required. |
| LLM advisory contour | implemented bounded | Inputs and outputs are bounded proposals; deterministic code owns calculations, verdicts, state changes, and permissions. Telegram cards expose whether a validation-bound setup has a separately persisted, accepted calculator advisory, uses a deterministic fallback, or is an authority-none farm research template. Product progress separately counts validated setups, research-observation cards and analyst inputs, so a packet of research images is not mislabeled as validated signals or model learning. Raw advisory prose never changes or enters subscriber levels. |
| Evidence/storage capability | implemented bounded | Content-bound archives, synthetic migration, and plan-digest-bound backup retention exist. An off-by-default exact-root capability now seals bounded farm stdout, journals, lineage, invocation metadata, and selected derived audit streams at writer boundaries; it uses the same content-addressed catalog, releases sources only after restore proof, retains compact recent/semantic projections, and fails closed on archive or budget loss. Its shared OS lock applies one monotonic wait budget across both in-process and interprocess contention, so a short concurrent contour write is retried while a persistent holder still fails closed. Private activation endurance remains operationally unproved. |
| Paper-card delivery | implemented bounded | Preview, deduplication, and guarded delivery exist; recipients, content, and acknowledgement state stay private. A chart card is one bounded Telegram `sendPhoto` effect whose caption contains the complete card text, eliminating the former photo-then-text partial-delivery window. Overlong captions fail before transport. A proven connection-establishment failure is retryable because no request reached Telegram; every post-connect unknown remains fail-closed. A successful Telegram response is authoritative even if the runtime log sink fails immediately afterward; the returned message id still reaches the delivery outbox. Current-attempt and carried ambiguous ACK counts are separate so a new failure stops the canary while historical operator-recovery debt remains blocked from replay. |
| Execution denial boundary | implemented | No supported entrypoint grants live order authority. |

RCC process liveness is now a strict 15-second owned-handle lane. It never
opens SQLite, reads runtime status files, inventories listeners, walks process
ancestry, or reopens a PID. Authority/fencing and filesystem/Telegram status
have independent supervised freshness clocks. Every lane publishes its exact
active stage and elapsed time in the minimal heartbeat, so a blocked I/O probe
fails under its own name without fabricating a stopped contour.

Expired process ownership survives a host reboot as fencing evidence, but it
cannot block the next canonical owner merely because Windows reused the old PID
for a process whose identity is not readable. Acquisition may reclaim that row
only when the persisted process start is provably before the current host boot
and the lease is already expired. Same-boot probe failures, live identities and
unexpired leases remain fail-closed, and the next fence stays monotonic.

Priority validation publication now wakes the canonical full product cycle
immediately after a new current generation is atomically complete. This avoids
waiting the ordinary 180-second cadence before paper consumers can verify the
new authority; a stop intent still wins and startup deadlines are unchanged.

The generation-bound delivery, analyst routing, role reconciliation, setup
memory, calibration, and quality report now publish the mandatory product
checkpoint before optional calculator and broad role-review LLM maintenance.
Those advisory stages remain bounded and observable after T+0; they cannot
consume the complete cold-start budget or grant product/trading authority.

The detailed ownership, evidence paths, missing proof, and next gate for every
row are canonical in the [Trading Portfolio Roadmap](docs/trading-portfolio-roadmap.md).

## Open Evidence Gates

1. **Startup-memory critical path:** merge and independently verify the
   non-authoritative cold-cache repair, then prove a cold accelerator-cache
   backfill cannot delay T+0 while its later progress remains observable. A
   complete known-bad snapshot is still mandatory current-product authority;
   missing or corrupt authority must block rather than be inferred empty.
   Earlier Package06 storage-capacity blockage is not the current launch
   blocker; ordinary retention proof remains a separate operational concern.
2. **Operational reliability:** complete the bounded paper-only canary without
   a real hard fail. Its preflight must first prove the reviewed backup/free-space
   budget. A clean unit-test suite is not runtime proof.
3. **Data and lifecycle continuity:** demonstrate that missing public data,
   interrupted work, delivery ambiguity, and recovery preserve lineage and do
   not manufacture outcomes or duplicates.
4. **Validation service SLO:** prove during the paper-only canary that fresh
   product work remains bounded and reaches current generation independently of
   historical debt. Prove that the bounded historical lane produces canonical
   setup-memory/validator-review evidence without replacing Telegram authority;
   service and net-drain failure stays degraded unless DB, storage, or current
   product safety is affected.
5. **Paper authority cutover:** after all product-chain phases merge, prove
   quiescent backup/restore, shadow parity, exact-revision marker activation,
   writer/fence identity, and rollback readiness before RCC launch.
6. **Quality calibration:** only after reliability, measure signal, validator,
   role, and LLM advisory quality against immutable held-out evidence.

GitHub issue #224 tracks end-to-end paper-only observation. Issue #155 remains
a later calibration task; it is not evidence that policies are ready to change.

## Explicitly Not Supported

- Live trading, real-money orders, private account actions, or private exchange
  endpoints.
- Profitability, signal-service, investment-advice, or live-readiness claims.
- LLM authority over prices, parameters, validation verdicts, lifecycle state,
  process control, credentials, or execution.
- Publication of private datasets, strategy parameters, candidate rankings,
  runtime logs, prompts/responses, recipients, or operational evidence.

## Verification Boundary

Use [docs/entrypoints.md](docs/entrypoints.md) only as a command contract. A
runtime start still requires a fresh external owner-authority manifest and the
[Farm Runbook](docs/farm_runbook.md) preflight. Public documentation never
grants process authority.

The development sequence is maintained in [ROADMAP.md](ROADMAP.md).
