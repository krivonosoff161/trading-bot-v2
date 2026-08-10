# Current State

Status: **CURRENT**

- Verified: 2026-08-10
- Verified against: `ee927237e6ba0f1e49261cdf6030bb4f5401401e`
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

## Implemented Public Contracts

| Capability | Status | Evidence ceiling |
|---|---|---|
| Public research ingestion | implemented bounded | Adapters and degraded modes are tested. Canonical scanner passes bound article resolution by both per-fetch and total wall budgets, publish progress only after completed documents, preserve deferred rows in the durable buffer, and expose bounded deferral as degraded rather than fabricating a signal. Continuous provider availability is not proved. |
| Strategy Lab and experiment registry | implemented bounded | Bounded scheduling, lineage, and candidate records exist; a long reliability window remains open. |
| Deterministic simulation | implemented bounded | Declared truth tiers and synthetic parity are covered; full market execution fidelity is not claimed. |
| Independent validation bridge | implemented bounded | `honest-backtest` can try to falsify a candidate; a pass means only not rejected. |
| Fenced candidate lifecycle | implemented bounded | Owner, fence, claim, generation, and idempotency contracts are tested. Continuous validation maintenance honors the configured batch capacity, scans a bounded window past terminal orphan/ineligible and artifact-unavailable head tasks, and applies upstream classification backpressure at a configurable high-water mark without discarding classify work. Aggregate backlog, oldest age, arrival, service, and drain estimates are observable; a finite retry budget terminalizes repeated failures. A batch with no exportable candidate preserves a current-code generation, but replaces a `code_stale` generation with one idempotent current-code `ready_empty` authority after rechecking active ownership; pending, invalid, unavailable, and ambiguous states remain fail-closed. Private runtime continuity and SLO compliance remain operational questions. |
| Paper observation | implemented bounded | Current-generation authority is content-verified once per cycle, active cards are loaded directly without a historical catalog scan, and signal evaluation preserves prefix no-lookahead semantics inside a declared-history-bounded recent horizon with completed-chunk progress and fail-closed cancellation. Public-data failures are classified as `provider_error`, `data_gap`, or `genuine_no_market_data`; they preserve the active observation, append separate operational incident/recovery evidence, and are censored from family ranking, paper/product training, setup memory, adaptive consumers, and LLM outcome review. The canonical farm requires an explicitly activated Paper Evidence v2 cutover, binds its writer lease to the same owner/process identity, atomically promotes bridge through projection, and blocks stale-generation Telegram delivery. Generation-bound preview, training, lineage, outcome retest and actual Telegram delivery complete before bounded analyst, role and setup-memory maintenance. The hot analyst path reads only non-empty current inputs and dispatches only the exact current-generation role IDs it produced; historical role evidence directories are not work queues. Setup-outcome memory now reuses a derived classifier-version/context-bound cache only when the complete candidate source digest and run-artifact identity still match. A prior complete snapshot can bootstrap unchanged pre-snapshot rows once; new, changed, missing, or invalidated run groups are deterministically reread, and cache/recompute counts are product-progress evidence. The cache is not authority and never bypasses current-generation training selection. Stop and claim-failure checks surround these stages, and only the final completed product cycle can publish farm readiness. Current lifecycle readers reuse one validated generation snapshot and do not fall back to historical validation cards while a current generation is pending or stale. Its producer generation accepts only validation-bound `pfr_farm` members. Broad `farm` observations cannot self-grant paper authority, enter the v2 bridge, account, lifecycle, or training surfaces; they may reach the existing Telegram preview only as explicitly labelled `farm_calculated` research cards under a separate content-hash freshness envelope with authority `none`. A changed source snapshot blocks delivery, malformed PFR identity still fails closed, and aggregate membership counts remain observable. Continuous private-runtime recovery remains unproved. |
| Research Control Center | implemented bounded | The canonical paper-only supervisor separates process/authority, database, real product-progress and Telegram poll-liveness lanes. Before its first heartbeat it publishes revision-bound, digest-verified startup stages; only sanitized exception types are retained, and a dead or PID-reused process fails startup immediately instead of consuming the full readiness budget. T+0 requires completed scanner and the final farm product-cycle checkpoint from the current run plus an identity-matched, fresh successful Telegram poll; an intermediate delivery checkpoint or live PID alone is insufficient. The final checkpoint exposes delivery ambiguity, advisory/role degradation, analyst routing, setup-memory refresh, and storage-maintenance state as safe aggregates. Its accepted report carries the immutable launch-time product boundary into steady monitoring, which re-verifies component sequences instead of rebasing the run at T+0. After T+0 a content-safe `pending` validation publication remains an observable `product_transitioning` state while the fenced worker makes real progress; it does not re-enter the startup timeout. RCC and external canary adapters share one fail-closed post-T+0 classifier, so an arbitrary `ready=false` cannot be mistaken for that bounded transition. A new external ACK ambiguity, stale Telegram polling, stale stages, worker/claim failure, validation SLO breach, generation corruption, storage-maintenance failure, or technical learning leakage fails closed. Carried ambiguous ACK debt remains visible but is never retried automatically. A completed zero-signal pass is honest idle and bounded provider/advisory degradation remains observable. A fresh 48-hour canary is still required. |
| LLM advisory contour | implemented bounded | Inputs and outputs are bounded proposals; deterministic code owns calculations, verdicts, state changes, and permissions. Telegram cards expose whether a validation-bound setup has a separately persisted, accepted calculator advisory, uses a deterministic fallback, or is an authority-none farm research template. Product progress separately counts validated setups, research-observation cards and analyst inputs, so a packet of research images is not mislabeled as validated signals or model learning. Raw advisory prose never changes or enters subscriber levels. |
| Evidence/storage capability | implemented bounded | Content-bound archives, synthetic migration, and plan-digest-bound backup retention exist. An off-by-default exact-root capability now seals bounded farm stdout, journals, lineage, invocation metadata, and selected derived audit streams at writer boundaries; it uses the same content-addressed catalog, releases sources only after restore proof, retains compact recent/semantic projections, and fails closed on archive or budget loss. Its shared OS lock applies one monotonic wait budget across both in-process and interprocess contention, so a short concurrent contour write is retried while a persistent holder still fails closed. Private activation endurance remains operationally unproved. |
| Paper-card delivery | implemented bounded | Preview, deduplication, and guarded delivery exist; recipients, content, and acknowledgement state stay private. A successful Telegram response is authoritative even if the runtime log sink fails immediately afterward; the returned message id still reaches the delivery outbox. Current-attempt and carried ambiguous ACK counts are separate so a new failure stops the canary while historical operator-recovery debt remains blocked from replay. |
| Execution denial boundary | implemented | No supported entrypoint grants live order authority. |

Priority validation publication now wakes the canonical full product cycle
immediately after a new current generation is atomically complete. This avoids
waiting the ordinary 180-second cadence before paper consumers can verify the
new authority; a stop intent still wins and startup deadlines are unchanged.

The detailed ownership, evidence paths, missing proof, and next gate for every
row are canonical in the [Trading Portfolio Roadmap](docs/trading-portfolio-roadmap.md).

## Open Evidence Gates

1. **Storage containment:** apply the reviewed retention plan only after
   post-merge code verification, preserve one integrity/restore-verified full
   generation, prove archive restoration and second-apply idempotence, then
   enforce the storage budget before runtime.
2. **Operational reliability:** complete the bounded paper-only canary without
   a real hard fail. Its preflight must first prove the reviewed backup/free-space
   budget. A clean unit-test suite is not runtime proof.
3. **Data and lifecycle continuity:** demonstrate that missing public data,
   interrupted work, delivery ambiguity, and recovery preserve lineage and do
   not manufacture outcomes or duplicates.
4. **Validation service SLO:** prove during the paper-only canary that the
   configured service capacity drains rather than grows the validation backlog,
   the oldest-age SLO remains bounded, and backpressure never loses classify work.
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
